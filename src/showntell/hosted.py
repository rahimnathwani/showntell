"""Hosted mode: one server for a company, behind sign-in (docs/hosted-design.md).

Everything here is used only with `SHOWNTELL_MODE=hosted`. `check_config` refuses to start on
unsafe settings; `Hosted` carries what the store, service and routes need (limits, identity,
the internal URL headless Chromium uses); `HostedGuard` sits in front of every route.
"""

from __future__ import annotations

import asyncio
import collections
import functools
import ipaddress
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .config import Config, Limits
from .identity import SCOPE_KEY, BrowserSession, Identity, StubLogin, User

log = logging.getLogger("showntell.hosted")

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
# Styles stay inline-capable: Mermaid writes <style> into its SVG and KaTeX sets style attributes.
# Script never is; draw.io's viewer evaluates nothing it needs, so no 'unsafe-eval' either.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:"
)
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
]
CSRF_HEADER = "x-csrf-token"
OPEN_PATHS = ("/static/", "/health", "/dev/login")
WRITING_STALE_S = 3600.0  # a walkthrough untouched this long no longer counts as in progress


@functools.cache
def _texts() -> dict:
    p = Path(__file__).parent / "text" / "hosted" / "replies.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def say(name: str, default: str, **kw) -> str:
    """Agent-facing hosted-mode text from text/hosted/replies.json."""
    template = _texts().get(name, default)
    try:
        return template.format(**kw)
    except (KeyError, IndexError):
        return default.format(**kw)


def _is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_config(cfg: Config) -> None:
    """Refuse to start in hosted mode on settings that would be unsafe or cannot work."""
    url = urlparse(cfg.public_url or "")
    if not cfg.public_url:
        raise SystemExit("hosted mode needs SHOWNTELL_PUBLIC_URL, the HTTPS origin users add (e.g. https://showntell.example.com)")
    if url.scheme not in ("https", "http") or not url.hostname or url.path not in ("", "/") or url.query:
        raise SystemExit(f"SHOWNTELL_PUBLIC_URL={cfg.public_url!r} must be an origin such as https://showntell.example.com")
    if url.scheme == "http" and not _is_loopback(url.hostname):
        raise SystemExit("SHOWNTELL_PUBLIC_URL must be https:// (http:// only on localhost, for trials)")
    if cfg.deliver != "link":
        raise SystemExit("hosted mode delivers links: unset SHOWNTELL_DELIVER")
    if _env_set("OIDC"):
        raise SystemExit("sign-in with SHOWNTELL_OIDC is not built yet (hosted mode phase 2); for a local trial use SHOWNTELL_DEV_USERS")
    if not cfg.dev_users.strip():
        raise SystemExit(
            "hosted mode has no sign-in yet (phase 2). For a trial on this machine set SHOWNTELL_DEV_USERS to a comma-separated list "
            "of emails, with a loopback SHOWNTELL_HOST and an http://localhost public URL"
        )
    if not cfg.loopback or not _is_loopback(url.hostname):
        raise SystemExit(
            "SHOWNTELL_DEV_USERS (the dev login) works only with a loopback SHOWNTELL_HOST and a localhost SHOWNTELL_PUBLIC_URL"
        )


def _env_set(name: str) -> bool:
    import os

    return bool(os.environ.get(f"SHOWNTELL_{name}", "").strip())


def dev_emails(cfg: Config) -> list[str]:
    return [e.strip().lower() for e in cfg.dev_users.split(",") if e.strip()]


def domains(cfg: Config) -> list[str]:
    return [d.strip().lower().lstrip("@") for d in cfg.allowed_domains.split(",") if d.strip()]


class RateLimiter:
    """At most `per_min` events per key in any sliding minute."""

    def __init__(self, per_min: int) -> None:
        self.per_min = per_min
        self._hits: dict[tuple, collections.deque] = {}

    def allow(self, key: tuple) -> bool:
        if self.per_min <= 0:
            return True
        now = time.monotonic()
        q = self._hits.setdefault(key, collections.deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_min:
            return False
        q.append(now)
        return True


@dataclass
class Hosted:
    """What hosted mode adds to the store: limits, who is calling, and how headless Chromium reaches the pages."""

    limits: Limits
    identity: Identity
    internal_url: str  # where this process listens, for its own Chromium
    public_url: str
    allowed_domains: list[str] = field(default_factory=list)
    rate: RateLimiter | None = None
    _browsers: asyncio.Semaphore | None = None

    def __post_init__(self) -> None:
        self.rate = self.rate or RateLimiter(self.limits.calls_per_min)

    @property
    def browsers(self) -> asyncio.Semaphore:
        """Chromium pages open at once, server-wide (created on the running loop)."""
        if self._browsers is None:
            self._browsers = asyncio.Semaphore(max(1, self.limits.browsers))
        return self._browsers

    def capture_session(self, owner: User, ttl: float) -> BrowserSession:
        """A short browser session for the owner, so headless Chromium can open their page."""
        return self.identity.sessions.create(owner, ttl=ttl)


def build(cfg: Config) -> Hosted:
    """The hosted context for `serve`, after `check_config`. Phase 1 knows only the dev login."""
    login = StubLogin.from_emails(dev_emails(cfg), cfg.data_dir)
    identity = Identity(login, secure_cookie=cfg.public_url.startswith("https://"))
    host = cfg.host if cfg.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    internal = f"http://{'[' + host + ']' if ':' in host else host}:{cfg.port}"
    banner = "\n".join(
        f"    {email}: MCP header 'Authorization: Bearer {tok}'  browser {cfg.base_url}/dev/login?token={tok}"
        for email, tok in login.tokens().items()
    )
    log.warning(
        "\n%s\nDEV LOGIN IS ON (SHOWNTELL_DEV_USERS): anyone on this machine can act as these users. Never use it in production.\n%s\n%s",
        "!" * 78,
        banner,
        "!" * 78,
    )
    return Hosted(cfg.limits, identity, internal, cfg.base_url, domains(cfg))


# -- the guard ------------------------------------------------------------------------------------


class HostedGuard:
    """In front of every route in hosted mode.

    - **Host:** the public URL's host, or loopback (this process's own Chromium).
    - **Who:** `/mcp` needs a bearer token; pages need the session cookie. The user goes into
      the ASGI scope under `identity.SCOPE_KEY` for routes and tools to check ownership.
    - **Gone:** the shim's `/api/rpc/*`.
    - **POSTs from pages:** same origin, JSON, and the session's CSRF token in `X-CSRF-Token`.
    - **Headers:** the CSP and its companions on every response.
    """

    def __init__(self, app, cfg: Config, hosted: Hosted, on_shutdown=None) -> None:
        self.app = app
        self.hosted = hosted
        self.identity = hosted.identity
        self.on_shutdown = on_shutdown
        pub = urlparse(cfg.public_url)
        self.public_host = (pub.hostname or "").lower()
        self.origins = {f"{pub.scheme}://{pub.netloc}".lower(), hosted.internal_url.lower()}

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
        send = _with_headers(send)
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        host = headers.get("host", "").lower()
        hostname = host[1 : host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0]
        if hostname != self.public_host and not _is_loopback(hostname):
            return await deny(send, 421, "unknown host")
        path = scope.get("path", "")
        if path.startswith("/api/rpc"):
            return await deny(send, 404, "Not found")
        if path == "/mcp" or path.startswith("/mcp/"):
            user = self.identity.from_bearer(headers)
            if user is None:
                return await deny(send, 401, "sign in required", [(b"www-authenticate", b'Bearer realm="showntell"')])
            scope[SCOPE_KEY] = user
            return await self.app(scope, receive, send)
        session = self.identity.from_cookie(headers)
        if session is not None:
            scope[SCOPE_KEY] = session.user
            scope["showntell.csrf"] = session.csrf
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            origin = (headers.get("origin") or "").lower()
            if origin and origin not in self.origins:
                return await deny(send, 403, "cross-origin request")
            if headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                return await deny(send, 403, "cross-site request")
            if not headers.get("content-type", "").startswith("application/json"):
                return await deny(send, 415, "JSON only")
            if session is None or not secrets.compare_digest(headers.get(CSRF_HEADER, ""), session.csrf):
                return await deny(send, 403, "missing or wrong CSRF token")
        if session is None and not path.startswith(OPEN_PATHS):
            return await deny(send, 401, "Sign in to see this page.")
        return await self.app(scope, receive, send)


def _with_headers(send):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            have = {k.lower() for k, _ in message.get("headers", [])}
            message["headers"] = list(message.get("headers", [])) + [(k, v) for k, v in SECURITY_HEADERS if k not in have]
        await send(message)

    return wrapped


async def deny(send, status: int, text: str, extra: list | None = None) -> None:
    body = text.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode()), *(extra or [])],
        }
    )
    await send({"type": "http.response.body", "body": body})


def user_of(scope_or_request) -> User | None:
    """The signed-in user the guard put on a request, or None (always None in local mode)."""
    scope = getattr(scope_or_request, "scope", scope_or_request)
    return scope.get(SCOPE_KEY) if isinstance(scope, dict) else None
