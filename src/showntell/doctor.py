"""`showntell doctor`: check the install, models, speech speed, port and daemon."""

from __future__ import annotations

import platform
import shutil
import socket
import sys
import time

from . import __version__, daemon
from .config import Config

# Until showntell is on PyPI, where this becomes `uvx showntell mcp`.
INSTALL = "uvx --from git+https://github.com/rahimnathwani/showntell@v0.2.2 showntell mcp"
# The same install with the browser that draws slides: the `video` extra adds Playwright, and
# `playwright install chromium` fetches its Chromium, about 100 MB, once.
VIDEO_FROM = "'showntell[video] @ git+https://github.com/rahimnathwani/showntell@v0.2.2'"
INSTALL_VIDEO = f"uvx --from {VIDEO_FROM} showntell mcp"
INSTALL_BROWSER = f"uvx --from {VIDEO_FROM} playwright install chromium"
INSTALL_EXPORT = f"uvx --from {VIDEO_FROM} showntell export <id> talk.mp4"
# Shown by finish (where the daemon runs `mcp`) and by MP4 export from the command line alike.
BROWSER_HINT = (
    "run showntell with the video extra and fetch the browser once:\n"
    f"    {INSTALL_VIDEO}   (or, to export: {INSTALL_EXPORT})\n    {INSTALL_BROWSER}"
)

OK, WARN, FAIL = "ok", "warn", "fail"


def _row(status: str, name: str, detail: str) -> None:
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
    print(f"  {mark} {name:<16} {detail}")


def _chromium() -> str:
    """Path of Playwright's Chromium, or '' when Playwright or its browser is missing."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception:
        return ""
    from pathlib import Path

    return path if Path(path).is_file() else ""


def _browser_check() -> tuple[str, str, str]:
    """finish_walkthrough draws every slide in Playwright's Chromium. Without it the layout check,
    the pictures of alike slides, the poster and MP4 export are off; the rest works, so a warning."""
    if _chromium():
        return OK, "slide checks", "Playwright Chromium found: finish_walkthrough draws every slide; MP4 export can render"
    return (
        WARN,
        "slide checks",
        (
            "Playwright Chromium not found: finish_walkthrough does not draw the slides, so the agent gets no layout "
            "findings, no pictures of alike slides and no poster, and MP4 export is off. To add it, " + BROWSER_HINT
        ),
    )


def _ffmpeg_check() -> tuple[str, str, str]:
    """MP4 export needs ffmpeg and ffprobe; HTML and folder exports do not."""
    missing = [x for x in ("ffmpeg", "ffprobe") if not shutil.which(x)]
    if missing:
        return WARN, "mp4 export", "needs " + " and ".join(missing) + " (for example `brew install ffmpeg`); HTML export works without"
    return OK, "mp4 export", "ffmpeg and ffprobe found"


def _hosted_checks(cfg: Config) -> list[tuple[str, str, str]]:
    """With SHOWNTELL_MODE=hosted: the settings, the provider's discovery document and Chromium's sandbox."""
    from . import hosted

    try:
        hosted.check_config(cfg)
    except SystemExit as e:
        return [(FAIL, "hosted settings", str(e))]
    rows = []
    if cfg.oidc:
        from .oidc import issuer_for

        issuer = issuer_for(cfg.oidc, cfg.entra_tenant, cfg.oidc_issuer)
        rows.append((OK, "hosted settings", f"sign-in with {cfg.oidc} ({issuer}); MCP at {cfg.base_url}/mcp"))
        try:
            import httpx

            meta = httpx.get(issuer + "/.well-known/openid-configuration", timeout=10).raise_for_status().json()
            if meta.get("issuer", "").rstrip("/") != issuer:
                rows.append((FAIL, "sign-in provider", f"its discovery document names issuer {meta.get('issuer')!r}, not {issuer!r}"))
            else:
                rows.append((OK, "sign-in provider", "discovery document found; issuer matches"))
        except Exception as e:  # noqa: BLE001 - unreachable or not a provider
            rows.append((FAIL, "sign-in provider", f"{issuer}/.well-known/openid-configuration: {type(e).__name__}: {e}"))
    else:
        rows.append((WARN, "hosted settings", "the dev login (SHOWNTELL_DEV_USERS) is on: a trial on this machine only, never production"))
    rows.append(_sandbox_check())
    return rows


def _sandbox_check() -> tuple[str, str, str]:
    """Hosted mode keeps Chromium's own sandbox on; it needs unprivileged user namespaces."""
    if not _chromium():
        return WARN, "chromium sandbox", "Playwright Chromium not found, so nothing to check"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch(chromium_sandbox=True).close()
    except Exception as e:  # noqa: BLE001
        first = str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__
        return FAIL, "chromium sandbox", f"Chromium will not start with its sandbox ({first}); see docs/self-hosting.md"
    return OK, "chromium sandbox", "Chromium starts with its sandbox on"


def doctor(cfg: Config, args) -> None:
    print(f"showntell {__version__} on Python {platform.python_version()}, {platform.system()} {platform.machine()}")
    problems = 0

    # data folder
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        _row(OK, "data folder", str(cfg.data_dir))
    except OSError as e:
        _row(FAIL, "data folder", f"{cfg.data_dir}: {e}")
        problems += 1

    # git, used for diffs read from the project
    if not cfg.hosted:  # a hosted server never reads git
        _row(OK if shutil.which("git") else WARN, "git", shutil.which("git") or "not found: diffs from git revisions will not work")

    # the finish-time layout check, and MP4 export
    _row(*_browser_check())
    _row(*_ffmpeg_check())

    if cfg.hosted:
        for row in _hosted_checks(cfg):
            _row(*row)
            problems += row[0] == FAIL

    # speech backend and models
    from .speech import Status, make_backend
    from .speech.mlx_backend import available as mlx_available

    kind = getattr(args, "speech", None) or cfg.speech
    if kind == "auto":
        kind = "mlx" if mlx_available() else "onnx"
    _row(OK, "speech backend", kind + (" (Apple silicon)" if kind == "mlx" else ""))
    status = Status()
    backend = make_backend(kind, cfg.data_dir, status)
    t0 = time.time()
    try:
        try:
            backend.load()
            load_s = time.time() - t0
            backend.synth("Warming up.", "af_heart", 1.0)
        except Exception as e:
            if kind != "mlx" or (getattr(args, "speech", None) or cfg.speech) != "auto":
                raise
            # The daemon does the same: it falls back to ONNX when MLX cannot speak.
            _row(WARN, "speech backend", f"MLX failed ({type(e).__name__}: {e}); the daemon speaks with ONNX instead")
            backend = make_backend("onnx", cfg.data_dir, status)
            t0 = time.time()
            backend.load()
            load_s = time.time() - t0
            backend.synth("Warming up.", "af_heart", 1.0)
        t1 = time.time()
        audio, tokens = backend.synth("showntell checks that the voice model works and how fast it speaks.", "af_heart", 1.0)
        compute = time.time() - t1
        dur = len(audio) / 24000
        rtf = dur / compute if compute else 0
        _row(OK, "voice model", f"loaded in {load_s:.1f} s; speaks {rtf:.0f}x faster than real time")
        if not tokens:
            _row(WARN, "word timings", "the backend returned no word timings; the pointer will be approximate")
        else:
            _row(OK, "word timings", f"{len(tokens)} timed words in a {dur:.1f} s sentence")
        if rtf < 2:
            _row(WARN, "speed", "speech is slow on this machine; long walkthroughs may pause while voicing")
    except Exception as e:
        _row(FAIL, "voice model", f"{type(e).__name__}: {e}")
        problems += 1

    # daemon and port
    lock = daemon.running(cfg)
    if lock:
        h = lock["health"]
        same = daemon.same_build(lock)
        _row(
            OK if same else WARN,
            "daemon",
            f"running at {lock['url']} (pid {lock['pid']}, version {lock.get('version')}, "
            f"speech {h['speech']['state']})"
            + (
                ""
                if same
                else "; it runs older code and will be replaced when idle"
                if daemon.newer_than(lock)
                else "; it runs newer code than this copy"
            ),
        )
    else:
        with socket.socket() as s:
            free = s.connect_ex(("127.0.0.1", cfg.port)) != 0
        _row(
            OK if free else WARN,
            "port",
            f"{cfg.port} is free" if free else f"{cfg.port} is in use by another program; showntell will pick the next free port",
        )
        _row(OK, "daemon", "not running; MCP clients start it on demand")

    print()
    if cfg.hosted:
        print(f"Add {cfg.base_url}/mcp to Claude (a claude.ai connector, `claude mcp add --transport http`, or Desktop).")
        sys.exit(1 if problems else 0)
    print("Connect an MCP client:")
    print(f"  Claude Code   claude mcp add --scope user showntell -- {INSTALL}")
    print(f"  any client    command: {INSTALL}   (stdio)")
    print(f"  with browser  {INSTALL_VIDEO}   then once: {INSTALL_BROWSER}")
    print(f"  over HTTP     {cfg.base_url}/mcp   (after `showntell serve`)")
    sys.exit(1 if problems else 0)
