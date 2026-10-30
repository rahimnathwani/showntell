"""Hosted sign-in with the company's provider: the `Login` that phase 1's stub stood in for.

Browsers sign in at `/login`, which sends them to the provider and back to `/oidc/callback`; a
user the preset's rule accepts gets a showntell session cookie. MCP clients are sent to
`/authorize` (oauth.py), and the user approves them at `/oauth/consent`, signed in the same way.
Bearer tokens on `/mcp` are showntell's own, looked up in `auth.db`.
"""

from __future__ import annotations

import html
import logging
import secrets
import time
from urllib.parse import quote, urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import oauth
from .authdb import AuthDB
from .identity import SESSION_COOKIE, Identity, User
from .oidc import Provider, SignInRefused, check_claims, new_login

log = logging.getLogger("showntell.signin")

LOGIN_COOKIE = "showntell_login"
LOGIN_S = 600


def local_path(next_: str) -> str:
    """Only a path on this server is a place to go back to after sign-in."""
    if not next_ or not next_.startswith("/") or next_.startswith("//") or "\\" in next_ or urlparse(next_).netloc:
        return "/"
    return next_


def page(title: str, body: str, status: int = 200, script: str = "", data: dict | None = None) -> HTMLResponse:
    attrs = "".join(f' data-{k}="{html.escape(str(v))}"' for k, v in (data or {}).items())
    tag = f'<script src="/static/{script}"></script>' if script else ""
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · showntell</title><link rel="icon" href="/static/icon.svg" type="image/svg+xml">
<style>
:root {{ color-scheme: dark; --bg: #0b0d12; --panel: #141922; --line: #232b38; --fg: #e9edf3; --muted: #8e98a9; --accent: #ffc857; --warn: #f0a35e; }}
@media (prefers-color-scheme: light) {{ :root {{ color-scheme: light; --bg: #f6f4ef; --panel: #fff; --line: #e4e0d6; --fg: #1d2129; --muted: #69707d; --accent: #b7791f; --warn: #a1561a; }} }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 560px; margin: 0 auto; padding: 64px 16px; }}
h1 {{ font-size: 24px; margin: 0 0 12px; }}
.card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 20px; margin: 20px 0; }}
.muted {{ color: var(--muted); font-size: 14px; }} .warn {{ color: var(--warn); }}
code {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: .92em; overflow-wrap: anywhere; }}
.actions {{ display: flex; gap: 12px; margin-top: 20px; }}
button {{ font: inherit; border-radius: 8px; padding: 8px 18px; cursor: pointer; border: 1px solid var(--line); background: none; color: var(--fg); }}
button.primary {{ background: var(--accent); border-color: var(--accent); color: #111; font-weight: 600; }}
</style></head><body{attrs}><main>{body}</main>{tag}</body></html>"""
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store"})


class OIDCLogin:
    """Sign-in with one provider (`oidc.Provider`), and the authorization server for MCP clients."""

    name = "provider sign-in"

    def __init__(self, provider: Provider, db: AuthDB, public_url: str, fetch_metadata=None, signin_max_s: float = 7 * 86400) -> None:
        self.provider = provider
        self.db = db
        self.signin_max_s = signin_max_s
        self.secure = public_url.startswith("https://")
        self.auth = oauth.AuthServer(db, public_url, self.allowed, fetch=fetch_metadata)

    def allowed(self, user: User) -> bool:
        """The sign-in rule again, on the claims from the user's last sign-in and today's settings,
        and that sign-in recent enough: only signing in asks the provider whether the account still
        exists, so a refresh chain must not outlive it by more than `signin_max_s`."""
        claims = self.db.claims(user.issuer, user.sub)
        last = self.db.last_sign_in(user.issuer, user.sub)
        if claims is None or last is None or (self.signin_max_s > 0 and time.time() - last[0] > self.signin_max_s):
            return False
        try:
            check_claims(self.provider.preset, claims, self.provider.domains, self.provider.tenant, self.provider.guests)
        except SignInRefused:
            return False
        return True

    def user_for_token(self, token: str) -> User | None:
        return self.auth.user_for_access_token(token)

    def login_url(self, next_: str) -> str:
        return f"/login?next={quote(next_, safe='')}"

    def challenge(self) -> str:
        return f'Bearer resource_metadata="{self.auth.resource_metadata_url}"'

    def _cookie(self, name: str, value: str, path: str, max_age: int) -> str:
        return f"{name}={value}; Path={path}; HttpOnly; SameSite=Lax; Max-Age={max_age}" + ("; Secure" if self.secure else "")

    def routes(self, identity: Identity) -> list:
        from .hosted import user_of

        async def login(request: Request) -> Response:
            started = new_login()
            nxt = local_path(request.query_params.get("next", "/"))
            try:
                url = await self.provider.login_url(started["state"], started["nonce"], started["verifier"])
            except Exception as e:  # noqa: BLE001 - the provider is down or misconfigured
                log.warning("sign-in provider unavailable: %s", e)
                return page(
                    "Sign-in unavailable",
                    "<h1>Sign-in is unavailable</h1><p>The sign-in provider did not answer. Try again in a minute.</p>",
                    502,
                )
            self.db.hold("login", {"nonce": started["nonce"], "verifier": started["verifier"], "next": nxt}, LOGIN_S, key=started["state"])
            r = RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
            r.headers.append("set-cookie", self._cookie(LOGIN_COOKIE, started["state"], "/oidc", LOGIN_S))
            return r

        async def callback(request: Request) -> Response:
            state = request.query_params.get("state", "")
            cookie = request.cookies.get(LOGIN_COOKIE, "")
            # The state must come back to the browser that started the sign-in (no login CSRF).
            if not state or not cookie or not secrets.compare_digest(state, cookie):
                return page(
                    "Sign-in failed",
                    '<h1>Sign-in failed</h1><p>This sign-in was started in another browser or has expired.</p><p><a href="/login">Sign in again</a></p>',
                    400,
                )
            started = self.db.take("login", state)
            if started is None:
                return page("Sign-in failed", '<h1>Sign-in expired</h1><p><a href="/login">Sign in again</a></p>', 400)
            if request.query_params.get("error"):
                return page("Sign-in cancelled", '<h1>Sign-in cancelled</h1><p><a href="/login">Sign in again</a></p>', 400)
            try:
                user, claims = await self.provider.finish(request.query_params.get("code", ""), started["verifier"], started["nonce"])
            except SignInRefused as e:
                log.warning("sign-in refused: %s", e)
                return page("Not allowed", f"<h1>You cannot use this server</h1><p>{html.escape(str(e))}</p>", 403)
            self.db.remember(user, claims)
            session = identity.sessions.create(user)
            log.info("signed in %s", user.email)
            r = RedirectResponse(started["next"], status_code=303, headers={"Cache-Control": "no-store"})
            r.headers.append("set-cookie", identity.cookie(session))
            r.headers.append("set-cookie", self._cookie(LOGIN_COOKIE, "", "/oidc", 0))
            return r

        async def logout(request: Request) -> Response:
            sid = request.cookies.get(SESSION_COOKIE, "")
            identity.sessions.end(sid)
            r = JSONResponse({"ok": True})
            r.headers.append("set-cookie", self._cookie(SESSION_COOKIE, "", "/", 0))
            return r

        async def consent(request: Request) -> Response:
            key = request.query_params.get("request", "")
            user = user_of(request)
            if user is None:
                return RedirectResponse(self.login_url(f"/oauth/consent?request={quote(key, safe='')}"), status_code=303)
            req = self.auth.consent_request(key)
            if req is None:
                return page("Request expired", "<h1>This request has expired</h1><p>Start the connection again from your app.</p>", 404)
            target = urlparse(req["redirect_uri"])
            local = target.hostname in ("localhost", "127.0.0.1", "::1")
            cid = req["client_id"]
            who = html.escape(req["client_name"] or "An app")
            source = (
                f"<code>{html.escape(urlparse(cid).hostname or '')}</code>"
                if oauth.is_metadata_url(cid)
                else "registered itself with this server"
            )
            warn = (
                '<p class="warn">It will send you back to an app on <b>your own computer</b>. Approve only if you just asked an app on this computer to connect.</p>'
                if local
                else ""
            )
            body = (
                f"<h1>Connect {who} to showntell?</h1>"
                f'<div class="card"><p><b>{who}</b> ({source}) wants to make and change walkthroughs as <b>{html.escape(user.email)}</b>.</p>'
                f"<p>After you choose, you go back to <code>{html.escape(target.scheme)}://{html.escape(target.netloc)}</code>.</p>{warn}"
                '<div class="actions"><button class="primary" id="allow">Allow</button><button id="deny">Deny</button></div></div>'
                '<p class="muted">It can then reach only your walkthroughs and those shared with you. Allow only apps you started yourself.</p>'
            )
            return page("Connect an app", body, script="consent.js", data={"csrf": request.scope.get("showntell.csrf", ""), "request": key})

        async def decide(request: Request) -> Response:
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "bad request"}, status_code=400)
            url = self.auth.decide(str(body.get("request", "")), user_of(request), body.get("approve") is True)
            if url is None:
                return JSONResponse({"error": "this request has expired"}, status_code=404)
            return JSONResponse({"redirect": url})

        return [
            *oauth.routes(self.auth),
            Route("/login", login, methods=["GET"]),
            Route("/oidc/callback", callback, methods=["GET"]),
            Route("/logout", logout, methods=["POST"]),
            Route("/oauth/consent", consent, methods=["GET"]),
            Route("/oauth/consent", decide, methods=["POST"]),
        ]
