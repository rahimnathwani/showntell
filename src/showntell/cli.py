"""Command line: `showntell mcp | serve | play | export | list | doctor`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import webbrowser
from pathlib import Path

from .config import Config
from .issues import line_text


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data", help="data folder (SHOWNTELL_DATA, default ~/.local/share/showntell)")
    p.add_argument("--port", type=int, help="port (SHOWNTELL_PORT, default 4747)")
    p.add_argument("--host", help="interface to bind (SHOWNTELL_HOST, default 127.0.0.1)")


def _deliver(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--deliver",
        choices=["link", "file"],
        help="link: the agent shares the player's link; file: the agent exports an MP4 and sends it, for agents in a "
        "sandbox or behind a chat app whose user cannot open links on this machine (SHOWNTELL_DELIVER, default link)",
    )


def _cfg(args) -> Config:
    cfg = Config()
    if getattr(args, "data", None):
        cfg.data_dir = Path(args.data).expanduser()
    if getattr(args, "port", None):
        cfg.port = args.port
    if getattr(args, "host", None):
        cfg.host = args.host
    for k in ("voice", "speech", "public_url", "deliver"):
        if getattr(args, k, None):
            setattr(cfg, k, getattr(args, k))
    if getattr(args, "open", False):
        cfg.open_browser = True
    return cfg


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="showntell", description="Narrated slide walkthroughs for coding agents, voiced on your machine.")
    sub = p.add_subparsers(dest="cmd")

    m = sub.add_parser("mcp", help="run as a stdio MCP server (starts or reuses the background daemon)")
    _common(m)
    _deliver(m)
    s = sub.add_parser("serve", help="run the daemon in the foreground (HTTP MCP at /mcp and the player)")
    _common(s)
    _deliver(s)
    s.add_argument("--voice", help="default voice (SHOWNTELL_VOICE)")
    s.add_argument("--speech", choices=["auto", "mlx", "onnx", "fake"], help="speech backend (SHOWNTELL_SPEECH)")
    s.add_argument("--public-url", help="base URL used in links when serving to other devices")
    s.add_argument("--open", action="store_true", help="open each walkthrough in the browser once playable")
    pl = sub.add_parser("play", help="voice and play a Markdown walkthrough file")
    _common(pl)
    pl.add_argument("file")
    pl.add_argument("--project", help="folder that file= paths are relative to (default: the git root or the file's folder)")
    pl.add_argument("--no-open", action="store_true")
    ex = sub.add_parser("export", help="write a walkthrough as one HTML file, a static folder, or an MP4")
    _common(ex)
    ex.add_argument("source", help="a walkthrough id, link or Markdown file")
    ex.add_argument("out", help="a file ending in .html (one page that plays offline), .mp4 (a video), or a folder")
    ex.add_argument("--mp3", action="store_true", help="folder: compress audio to MP3")
    ex.add_argument("--fps", type=int, default=30, help="MP4: frames per second (default 30)")
    ex.add_argument("--workers", type=int, help="MP4: browsers rendering frames in parallel (default min(4, CPUs))")
    ex.add_argument("--burn-captions", action="store_true", help="MP4: draw captions into the picture as well as the subtitle track")
    ex.add_argument(
        "--motion",
        action="store_true",
        help="MP4: keep the idle motion (drawings breathe, held close-ups creep in); slower, since no frame repeats",
    )
    ex.add_argument("--max-mb", type=float, default=0, help="MP4: shrink the video to fit this many MB, or fail (WhatsApp: 16)")
    ls = sub.add_parser("list", help="list recent walkthroughs")
    _common(ls)
    d = sub.add_parser("doctor", help="check models, speech speed, port and daemon")
    _common(d)
    d.add_argument("--speech", choices=["auto", "mlx", "onnx", "fake"])
    pr = sub.add_parser("prune", help="delete walkthroughs unchanged for a while, and audio nothing uses")
    _common(pr)
    pr.add_argument("--older-than", required=True, metavar="AGE", help="unchanged for at least this long: 60d, 8w or 12h")
    pr.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")
    st = sub.add_parser("stop", help="stop the background daemon; the next client starts this copy's")
    _common(st)
    au = sub.add_parser("autostart", help="macOS: start the daemon at login and keep it running")
    _common(au)
    au.add_argument("action", choices=["on", "off", "status"])
    sub.add_parser("version", help="print the version")

    args = p.parse_args(argv)
    if args.cmd is None:
        # MCP clients usually launch the bare command; a terminal user wants help.
        if not sys.stdin.isatty():
            args = p.parse_args(["mcp"])
        else:
            p.print_help()
            return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    for noisy in ("httpx", "httpx2", "mcp.server.streamable_http", "mcp.server.lowlevel.server", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = _cfg(args)
    if args.cmd == "serve":
        from .app import serve

        serve(cfg)
    elif args.cmd == "mcp":
        run_shim(cfg)
    elif args.cmd == "play":
        play(cfg, args)
    elif args.cmd == "export":
        from .export import export_cli

        export_cli(cfg, args)
    elif args.cmd == "list":
        list_cmd(cfg)
    elif args.cmd == "doctor":
        from .doctor import doctor

        doctor(cfg, args)
    elif args.cmd == "prune":
        prune_cmd(cfg, args)
    elif args.cmd == "stop":
        from . import daemon

        lock = daemon.stop(cfg)
        print(f"Stopped the daemon (pid {lock['pid']}, version {lock.get('version')})." if lock else "No daemon is running.")
    elif args.cmd == "autostart":
        autostart(cfg, args.action)
    elif args.cmd == "version":
        from . import __version__

        print(__version__)


def run_shim(cfg: Config) -> None:
    from . import daemon
    from .server import create_mcp
    from .service import RemoteService

    def say(msg: str) -> None:
        print(msg, file=sys.stderr)

    lock = daemon.ensure(cfg, log=say)
    service = RemoteService(f"http://127.0.0.1:{lock['port']}", lock["token"])
    mcp = create_mcp(service, project_dir_default=os.getcwd(), deliver=cfg.deliver)
    asyncio.run(mcp.run_stdio_async())


def _project_for(file: Path) -> Path:
    import subprocess

    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=file.parent, capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return file.parent


def file_meta(file: Path, text: str) -> dict:
    """Title, frame, voice and speed for a Markdown walkthrough file: front matter first,
    then the first heading, then the file name."""
    from .format import Parser

    meta = {"title": "", "frame": "landscape", "voice": "", "speed": 1.0}
    for b in Parser().feed(text):
        if b.kind == "front":
            meta["title"] = b.attrs.get("title", "")
            meta["frame"] = b.attrs.get("frame", "landscape")
            meta["voice"] = b.attrs.get("voice", "")
            try:
                meta["speed"] = float(b.attrs.get("speed", 1.0))
            except ValueError:
                pass
        elif b.kind == "heading":
            meta["title"] = meta["title"] or b.text
            break
    meta["title"] = meta["title"] or file.stem.replace("-", " ").replace("_", " ").title()
    return meta


def play(cfg: Config, args) -> None:
    from . import daemon
    from .service import RemoteService

    file = Path(args.file).expanduser().resolve()
    if not file.is_file():
        raise SystemExit(f"{file} not found")
    text = file.read_text()
    meta = file_meta(file, text)
    project = Path(args.project).resolve() if args.project else _project_for(file)
    lock = daemon.ensure(cfg, log=lambda m: print(m, file=sys.stderr))

    async def go() -> None:
        svc = RemoteService(f"http://127.0.0.1:{lock['port']}", lock["token"])
        r = await svc.start(meta["title"], meta["frame"], meta["voice"], meta["speed"], str(project))
        a = await svc.add(r["handle"], text)
        await svc.playable(r["handle"], 60)
        f = await svc.finish(r["handle"], None)
        # `play` opens the player itself, which runs the render and layout checks, so notes about
        # checks that have not run here (no browser, or not yet) do not apply.
        for issue in a["issues"] + f["issues"]:
            if issue.get("code") not in ("not_checked", "no_browser"):
                print(line_text(issue), file=sys.stderr)
        print(f["url"])
        if not args.no_open:
            webbrowser.open(f["url"])

    asyncio.run(go())


def list_cmd(cfg: Config) -> None:
    from .service import mmss

    walk = cfg.data_dir / "walkthroughs"
    if not walk.is_dir():
        print("No walkthroughs yet.")
        return
    import json

    from .export import timeline_total
    from .model import Walkthrough

    rows = []
    for d in walk.iterdir():
        st = d / "state.json"
        if st.is_file():
            data = json.loads(st.read_text())
            dur = timeline_total(Walkthrough.from_state(data))
            rows.append((data.get("updated", 0), data["id"], data.get("title", ""), len(data.get("slides", [])), dur))
    for _, wid, title, n, dur in sorted(rows, reverse=True)[:30]:
        print(f"{wid}  {mmss(dur):>5}  {n:>2} slides  {title}")


def prune_cmd(cfg: Config, args) -> None:
    import time

    from .prune import parse_age, prune

    try:
        age = parse_age(args.older_than)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    r = prune(cfg.data_dir, age, dry_run=args.dry_run)
    verb = "Would remove" if args.dry_run else "Removed"
    for w in r["walkthroughs"]:
        print(f"{w['id']}  {time.strftime('%Y-%m-%d', time.localtime(w['updated']))}  {w['title']}")
    print(f"{verb} {len(r['walkthroughs'])} walkthrough(s) and {r['audio_files']} audio file(s), {r['audio_bytes'] / 1e6:.1f} MB.")


LABEL = "dev.showntell.daemon"


def autostart(cfg: Config, action: str) -> None:
    """A LaunchAgent that runs `showntell serve` at login, so the voice model is always loaded."""
    import plistlib
    import subprocess

    if sys.platform != "darwin":
        raise SystemExit("autostart uses launchd and works on macOS only; use a systemd user unit elsewhere")
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    if action == "status":
        r = subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True, text=True)
        state = next((ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("state =")), "")
        print(f"autostart is {'on' if plist.exists() else 'off'}" + (f" ({state})" if state else ""))
        return
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    if action == "off":
        plist.unlink(missing_ok=True)
        print("autostart is off")
        return
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    log = str(cfg.data_dir / "daemon.log")
    env = {k: v for k, v in os.environ.items() if k.startswith(("SHOWNTELL_", "HF_"))}
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(
        plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": [sys.executable, "-m", "showntell", "serve", "--port", str(cfg.port)],
                "EnvironmentVariables": env,
                "RunAtLoad": True,
                "KeepAlive": True,
                "StandardOutPath": log,
                "StandardErrorPath": log,
            }
        )
    )
    r = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"launchctl refused the agent: {r.stderr.strip()}")
    print(f"autostart is on: the daemon starts at login. Logs: {log}")
    if "/uv/" in sys.executable and "/tools/" not in sys.executable:
        print("note: this runs from a uv cache folder; `uv tool install showntell` gives a path that survives cache cleaning")
