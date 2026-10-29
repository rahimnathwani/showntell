"""Who is calling, in hosted mode: the one place a request becomes a user.

A user's key is `(issuer, sub)`; the email is for display and for matching shares, never for
identity. `Identity` resolves an MCP request from its bearer token and a browser request from
its session cookie. Phase 1 has only `StubLogin`, a fixed list of users for tests and local
trials; sign-in with a company's provider replaces it without touching ownership code, which
sees only `User`.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Protocol

log = logging.getLogger("showntell.identity")

SESSION_COOKIE = "showntell_session"
SESSION_S = 12 * 3600
SCOPE_KEY = "showntell.user"  # the ASGI scope entry the guard fills for routes and tools
STUB_ISSUER = "showntell-dev-login"


@dataclass(frozen=True)
class User:
    issuer: str
    sub: str
    email: str  # verified email or UPN, lower case

    @property
    def key(self) -> tuple[str, str]:
        return (self.issuer, self.sub)

    def as_owner(self) -> dict:
        return {"iss": self.issuer, "sub": self.sub, "email": self.email}

    @classmethod
    def from_owner(cls, d: dict) -> User:
        return cls(d["iss"], d["sub"], d.get("email", ""))


@dataclass
class BrowserSession:
    id: str
    user: User
    csrf: str
    expires: float


class BrowserSessions:
    """Viewer sessions behind an HttpOnly cookie, each with its own CSRF token. In memory: a
    restart signs everyone out of the pages, never out of MCP."""

    def __init__(self, ttl: float = SESSION_S) -> None:
        self.ttl = ttl
        self._by_id: dict[str, BrowserSession] = {}

    def create(self, user: User, ttl: float | None = None) -> BrowserSession:
        self._sweep()
        s = BrowserSession(secrets.token_urlsafe(32), user, secrets.token_urlsafe(24), time.time() + (ttl or self.ttl))
        self._by_id[s.id] = s
        return s

    def get(self, sid: str) -> BrowserSession | None:
        s = self._by_id.get(sid or "")
        if s is None or s.expires < time.time():
            self._by_id.pop(sid or "", None)
            return None
        return s

    def end(self, sid: str) -> None:
        self._by_id.pop(sid, None)

    def _sweep(self) -> None:
        now = time.time()
        for k in [k for k, s in self._by_id.items() if s.expires < now]:
            del self._by_id[k]


class Login(Protocol):
    """Where users come from. Phase 2's provider sign-in implements the same three members."""

    name: str

    def user_for_token(self, token: str) -> User | None: ...

    def routes(self, identity: Identity) -> list:
        """Sign-in pages, as (path, methods, endpoint)."""
        ...


class Identity:
    """Resolves requests to users: bearer tokens for `/mcp`, the session cookie for pages."""

    def __init__(self, login: Login, sessions: BrowserSessions | None = None, secure_cookie: bool = True) -> None:
        self.login = login
        self.sessions = sessions or BrowserSessions()
        self.secure_cookie = secure_cookie

    def from_bearer(self, headers: dict[str, str]) -> User | None:
        auth = headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return self.login.user_for_token(token.strip())

    def from_cookie(self, headers: dict[str, str]) -> BrowserSession | None:
        morsel = SimpleCookie(headers.get("cookie", "")).get(SESSION_COOKIE)
        return self.sessions.get(morsel.value) if morsel else None

    def cookie(self, s: BrowserSession) -> str:
        flags = "; Secure" if self.secure_cookie else ""
        return f"{SESSION_COOKIE}={s.id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={int(s.expires - time.time())}{flags}"

    def routes(self) -> list:
        return self.login.routes(self)


class StubLogin:
    """A fixed list of users, each with a bearer token, for tests and trials on one machine.

    Turned on only by `SHOWNTELL_DEV_USERS` with a loopback bind and a loopback public URL
    (`hosted.check_config` refuses anything else), and announced in the log at every start.
    Tokens persist in `dev-logins.json` (mode 0600) in the data folder."""

    name = "dev login"

    def __init__(self, tokens: dict[str, str]) -> None:
        # token -> email
        self._users = {tok: email.strip().lower() for email, tok in tokens.items()}

    @classmethod
    def from_emails(cls, emails: list[str], data_dir: Path) -> StubLogin:
        path = data_dir / "dev-logins.json"
        saved: dict[str, str] = {}
        if path.is_file():
            try:
                saved = json.loads(path.read_text())
            except ValueError:
                saved = {}
        tokens = {e: saved.get(e) or f"dev-{secrets.token_urlsafe(24)}" for e in emails}
        path.write_text(json.dumps(tokens, indent=1))
        os.chmod(path, 0o600)
        return cls(tokens)

    def tokens(self) -> dict[str, str]:
        return {email: tok for tok, email in self._users.items()}

    def user_for_token(self, token: str) -> User | None:
        for tok, email in self._users.items():
            if secrets.compare_digest(tok, token):
                return User(STUB_ISSUER, email, email)
        return None

    def routes(self, identity: Identity) -> list:
        from starlette.responses import HTMLResponse, RedirectResponse

        async def login(request):
            user = self.user_for_token(request.query_params.get("token", ""))
            if user is None:
                return HTMLResponse("Unknown dev login token.", status_code=401)
            s = identity.sessions.create(user)
            log.warning("DEV LOGIN: browser session for %s", user.email)
            r = RedirectResponse("/", status_code=303)
            r.headers.append("set-cookie", identity.cookie(s))
            return r

        return [("/dev/login", ["GET"], login)]
