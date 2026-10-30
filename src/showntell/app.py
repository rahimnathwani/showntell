"""Assemble and run the daemon: speech worker, store, MCP over HTTP, web routes and the guard."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import socket
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from . import daemon
from .config import Config
from .server import add_routes, create_mcp
from .service import LocalService
from .speech import Narrator, Status, make_backend
from .store import Store

log = logging.getLogger("showntell")
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "[::1]", "::1"}


class Guard:
    """Host and Origin checks on every route, JSON-only POSTs, and an access token when the
    daemon is reachable from other machines."""

    def __init__(self, app, cfg: Config, on_shutdown=None) -> None:
        self.app = app
        self.on_shutdown = on_shutdown
        self.allowed = set(LOOPBACK_NAMES)
        if cfg.public_url:
            self.allowed.add((urlparse(cfg.public_url).hostname or "").lower())
        if not cfg.loopback and cfg.host not in ("0.0.0.0", "::"):
            self.allowed.add(cfg.host.lower())
        self.open_hosts = not cfg.loopback and not cfg.public_url  # bound wide without a known name
        self.token = cfg.access_token if (not cfg.loopback or cfg.public_url) else ""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan" and self.on_shutdown:

            async def receive_and_clean():
                msg = await receive()
                if msg["type"] == "lifespan.shutdown":
                    try:
                        self.on_shutdown()
                    except Exception:
                        log.exception("cleanup on shutdown failed")
                return msg

            return await self.app(scope, receive_and_clean, send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        host = headers.get("host", "").lower()
        hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        if hostname not in self.allowed and not self.open_hosts:
            return await _deny(send, 421, "unknown host")
        path = scope.get("path", "")
        client = (scope.get("client") or ("", 0))[0]
        local_client = client in ("127.0.0.1", "::1", "localhost") or client.startswith("127.")
        if path.startswith(("/mcp", "/api/rpc")) and not local_client:
            return await _deny(send, 403, "local clients only")
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            origin = headers.get("origin")
            if origin and origin != "null":
                oh = (urlparse(origin).hostname or "").lower()
                if oh not in {h.strip("[]") for h in self.allowed} and not (self.open_hosts and oh == hostname):
                    return await _deny(send, 403, "cross-origin request")
            if path.startswith(("/w/", "/api/rpc")) and not headers.get("content-type", "").startswith("application/json"):
                return await _deny(send, 415, "JSON only")
        if self.token and not path.startswith(("/mcp", "/api/rpc", "/health")):
            q = parse_qs(scope.get("query_string", b"").decode())
            given = (q.get("t") or [""])[0]
            cookie = SimpleCookie(headers.get("cookie", "")).get("showntell_t")
            ok_query = given and secrets.compare_digest(given, self.token)
            ok_cookie = cookie and secrets.compare_digest(cookie.value, self.token)
            if not (ok_query or ok_cookie):
                return await _deny(send, 401, "access token required: open the link you were given")
            if ok_query and not ok_cookie:

                async def send_with_cookie(message):
                    if message["type"] == "http.response.start":
                        message.setdefault("headers", [])
                        message["headers"] = list(message["headers"]) + [
                            (b"set-cookie", f"showntell_t={self.token}; Path=/; HttpOnly; SameSite=Strict".encode())
                        ]
                    await send(message)

                return await self.app(scope, receive, send_with_cookie)
        return await self.app(scope, receive, send)


async def _deny(send, status: int, text: str) -> None:
    body = text.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def pick_port(host: str, preferred: int, tries: int = 20) -> int:
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sock:
            if os.name != "nt":
                # As uvicorn binds: a port a stopped daemon left in TIME_WAIT is free to listen on.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"no free port between {preferred} and {preferred + tries - 1}")


def build_app(cfg: Config, service: LocalService, rpc_token: str, started: float, stop=None, cleanup=None):
    """The ASGI app: MCP at /mcp, the web routes, and the guard for the mode."""
    mcp = create_mcp(service, deliver=cfg.deliver)
    add_routes(mcp, service, rpc_token, started, stop=stop)
    h = service.store.hosted
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    if h:
        from .hosted import HostedGuard

        mcp._custom_starlette_routes.extend(h.identity.routes())  # sign-in pages and the OAuth endpoints
        pub = urlparse(cfg.public_url)
        hosts += [pub.netloc, f"{pub.hostname}:*"]
        origins.append(f"{pub.scheme}://{pub.netloc}")
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)
    inner = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=security, host=cfg.host)
    if h:
        return HostedGuard(inner, cfg, h, on_shutdown=cleanup)
    return Guard(inner, cfg, on_shutdown=cleanup)


def serve(cfg: Config) -> None:
    started = time.time()
    if cfg.hosted:
        from . import hosted

        hosted.check_config(cfg)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    held = daemon.instance_lock(cfg)
    if held is None:
        other = daemon.running(cfg)
        where = f" at {other['url']} (pid {other['pid']})" if other else ""
        raise SystemExit(f"showntell is already running for {cfg.data_dir}{where}")
    if (not cfg.loopback or cfg.public_url) and not cfg.access_token and not cfg.hosted:
        cfg.access_token = secrets.token_urlsafe(16)
    port = pick_port(cfg.host, cfg.port)
    if port != cfg.port:
        log.info("port %d is taken; using %d", cfg.port, port)
        cfg.port = port
    status = Status()
    narrator = Narrator(make_backend(cfg.speech, cfg.data_dir, status), cfg.audio_dir, status)
    narrator.lexicon = load_lexicon(cfg)
    narrator.start()
    suffix = f"?t={cfg.access_token}" if cfg.access_token else ""
    h = None
    if cfg.hosted:
        from . import hosted

        h = hosted.build(cfg)
    store = Store(
        cfg.data_dir, cfg.base_url, narrator, default_voice=cfg.voice, open_browser=cfg.open_browser, link_suffix=suffix, hosted=h
    )
    service = LocalService(store, layout_check=cfg.finish_check)
    rpc_token = secrets.token_urlsafe(24)
    holder: dict = {}

    def close_streams() -> None:
        # Open event streams never end on their own, and uvicorn waits for them before shutting
        # down, so a page left open kept the server (and the save in cleanup) from stopping.
        if store.loop:
            store.loop.call_soon_threadsafe(lambda: [s.close_streams() for s in list(store.sessions.values())])

    def stop() -> None:
        if holder.get("server"):
            holder["server"].should_exit = True
            close_streams()

    def cleanup() -> None:
        daemon.clear_lock(cfg)
        for s in list(store.sessions.values()):
            s.save_now()

    app = build_app(cfg, service, rpc_token, started, stop=stop, cleanup=cleanup)

    async def main() -> None:
        store.loop = asyncio.get_running_loop()
        # A stream that still holds on is cut after this long, so a stop always reaches cleanup.
        config = uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning", lifespan="on", timeout_graceful_shutdown=5)
        server = _Server(config, on_exit=close_streams)
        holder["server"] = server
        daemon.write_lock(cfg, rpc_token)

        async def keep_log_small() -> None:
            while True:
                daemon.cap_log(cfg.data_dir / "daemon.log")
                await asyncio.sleep(3600)

        holder["log"] = asyncio.create_task(keep_log_small())
        if h:
            holder["expire"] = asyncio.create_task(expire(store, h, cfg))
        log.info(
            "showntell %s at %s (MCP: http://localhost:%d/mcp, speech: %s)",
            _version(),
            cfg.base_url + "/" + suffix,
            cfg.port,
            narrator.backend.name,
        )
        try:
            await server.serve()
        finally:
            cleanup()

    asyncio.run(main())


class _Server(uvicorn.Server):
    """uvicorn's server, telling showntell when a signal asks it to stop."""

    def __init__(self, config: uvicorn.Config, on_exit) -> None:
        super().__init__(config)
        self.on_exit = on_exit

    def handle_exit(self, sig, frame) -> None:
        self.on_exit()
        super().handle_exit(sig, frame)


async def expire(store: Store, h, cfg: Config, every: float = 86400) -> None:
    """Hosted, daily: drop spent sign-in records, and walkthroughs unseen past the retention. A
    round that fails is logged, and the next one runs as planned."""
    from . import prune

    while True:
        try:
            db = getattr(h.identity.login, "db", None)
            if db is not None:
                db.sweep()
            if cfg.limits.retention_days > 0:
                gone = await asyncio.to_thread(prune.prune, cfg.data_dir, cfg.limits.retention_days * 86400)
                for row in gone["walkthroughs"]:
                    store.forget(row["id"])
                if gone["walkthroughs"]:
                    n, days = len(gone["walkthroughs"]), cfg.limits.retention_days
                    log.info("retention: deleted %d walkthrough(s) unseen for %g days", n, days)
        except Exception:
            log.exception("expiring sign-ins and old walkthroughs failed; trying again in %g h", every / 3600)
        await asyncio.sleep(every)


def load_lexicon(cfg: Config) -> dict[str, str]:
    """User word list: one `word = spoken form` per line."""
    out: dict[str, str] = {}
    if cfg.lexicon_file.is_file():
        for line in cfg.lexicon_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip()] = v.strip()
    return out


def _version() -> str:
    from . import __version__

    return __version__
