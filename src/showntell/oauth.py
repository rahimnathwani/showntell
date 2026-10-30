"""showntell as the OAuth authorization server for its own `/mcp` (MCP authorization, 2026-07-28).

MCP clients never see a Google or Entra token: they register (a client ID metadata document, or
dynamic registration), send the user to `/authorize` with PKCE S256 and a `resource`, and get
showntell's own opaque tokens bound to `<public url>/mcp`. The user signs in through the browser
session (signin.py) and approves the client on a consent page that names it and where it sends them.

The MCP Python SDK supplies the authorize, token, registration and revocation handlers and the
OAuth models; `AuthServer` is the provider they call. Written here instead: the metadata documents
(the SDK's lack `client_id_metadata_document_supported`, `authorization_response_iss_parameter_supported`
and the `none` client auth method), fetching client ID metadata documents, the consent step, and the
check of the token request's `resource` (the SDK's token handler does not pass it on).

| Token | Lifetime | Notes |
|---|---|---|
| authorization code | 5 min | one use; a second use revokes the tokens it gave |
| access token | 1 h | audience `<public url>/mcp`, refused for any other |
| refresh token | 30 days | rotates on every use; reusing a spent one revokes its whole chain |
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull, OAuthToken

from .authdb import AuthDB, digest
from .identity import User

log = logging.getLogger("showntell.oauth")

CODE_S = 300
ACCESS_S = 3600
REFRESH_S = 30 * 86400
CONSENT_S = 600
METADATA_MAX_BYTES = 16_384
METADATA_CACHE_S = (60, 300, 86400)  # least, default, most seconds a client metadata document is kept


def canonical(uri: str) -> str:
    """A resource URI as compared: scheme and host in lower case, no trailing slash."""
    u = urlparse((uri or "").strip())
    if not u.scheme or not u.netloc or u.fragment:
        return ""
    return f"{u.scheme.lower()}://{u.netloc.lower()}{u.path.rstrip('/')}" + (f"?{u.query}" if u.query else "")


def redirect_ok(uri: str) -> bool:
    """Redirect URIs must be HTTPS or loopback HTTP."""
    u = urlparse(uri)
    if u.fragment or not u.hostname:
        return False
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1"))


LOOPBACK = ("localhost", "127.0.0.1", "::1")


def same_redirect(registered: str, given: str) -> bool:
    """The redirect URI a client sends matches one it registered: exactly, or for loopback HTTP
    with any port (RFC 8252 7.3), since native clients such as Claude Code listen on a port picked
    at sign-in time. Scheme, host, path and query must still match."""
    if registered == given:
        return True
    a, b = urlparse(registered), urlparse(given)
    return (
        a.scheme == b.scheme == "http"
        and a.hostname in LOOPBACK
        and a.hostname == b.hostname
        and (a.path or "/") == (b.path or "/")
        and a.query == b.query
        and not b.fragment
        and not b.username
        and not b.password
    )


class Client(OAuthClientInformationFull):
    """The SDK's client record, with loopback redirect URIs matched on any port."""

    def validate_redirect_uri(self, redirect_uri):
        if redirect_uri is None or not self.redirect_uris:
            return super().validate_redirect_uri(redirect_uri)
        if any(same_redirect(str(u), str(redirect_uri)) for u in self.redirect_uris):
            return redirect_uri
        raise InvalidRedirectUriError(f"Redirect URI '{redirect_uri}' not registered for client")


def is_metadata_url(client_id: str) -> bool:
    u = urlparse(client_id or "")
    return u.scheme == "https" and bool(u.hostname) and u.path not in ("", "/") and not u.fragment and not u.username and not u.password


async def _public_host(host: str) -> None:
    """Refuse to fetch from this machine or a private network (SSRF)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError(f"{host} does not resolve") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise ValueError(f"{host} is not a public address")


async def fetch_client_metadata(url: str) -> tuple[dict, float]:
    """A client ID metadata document and how long to keep it: from a public host, no redirects,
    at most 16 KB of JSON."""
    await _public_host(urlparse(url).hostname or "")
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as c, c.stream("GET", url, headers={"Accept": "application/json"}) as r:
        if r.status_code != 200:
            raise ValueError(f"the document answered {r.status_code}")
        body = b""
        async for chunk in r.aiter_bytes():
            body += chunk
            if len(body) > METADATA_MAX_BYTES:
                raise ValueError("the document is over 16 KB")
        m = re.search(r"max-age=(\d+)", r.headers.get("cache-control", ""))
        least, default, most = METADATA_CACHE_S
        keep = min(most, max(least, int(m.group(1)))) if m else default
        if "no-store" in r.headers.get("cache-control", ""):
            keep = least
        return json.loads(body), keep


def client_from_metadata(url: str, doc: dict) -> Client:
    """Check a client ID metadata document and turn it into the SDK's client record."""
    if not isinstance(doc, dict) or doc.get("client_id") != url:
        raise ValueError("its client_id is not the URL it was fetched from")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(x, str) and redirect_ok(x) for x in uris):
        raise ValueError("redirect_uris must list HTTPS or loopback URIs")
    if not isinstance(doc.get("client_name"), str) or not doc["client_name"].strip():
        raise ValueError("it has no client_name")
    if doc.get("token_endpoint_auth_method", "none") != "none":
        raise ValueError("only public clients (token_endpoint_auth_method none) are supported")
    return Client(
        client_id=url,
        client_name=doc["client_name"][:100],
        client_uri=doc.get("client_uri") if isinstance(doc.get("client_uri"), str) and doc["client_uri"].startswith("https://") else None,
        redirect_uris=uris,
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=doc.get("scope") if isinstance(doc.get("scope"), str) else None,
    )


class AuthServer:
    """The SDK's `OAuthAuthorizationServerProvider`, on `auth.db`.

    `allowed(user)` is asked again at every code exchange and refresh: it applies the sign-in rule
    to the claims from the user's last sign-in under today's settings."""

    def __init__(
        self,
        db: AuthDB,
        public_url: str,
        allowed: Callable[[User], bool],
        fetch: Callable[[str], Awaitable[tuple[dict, float]]] | None = None,
    ) -> None:
        self.db = db
        self.issuer = public_url.rstrip("/")
        self.resource = self.issuer + "/mcp"
        self.allowed = allowed
        self.fetch = fetch or fetch_client_metadata
        self._documents: dict[str, tuple[OAuthClientInformationFull, float]] = {}

    # -- clients -------------------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if is_metadata_url(client_id):
            hit = self._documents.get(client_id)
            if hit and hit[1] > time.time():
                return hit[0]
            try:
                doc, keep = await self.fetch(client_id)
                client = client_from_metadata(client_id, doc)
            except Exception as e:  # noqa: BLE001 - any failure means an unknown client
                log.warning("client metadata document %s refused: %s", client_id[:200], e)
                return None
            self._documents[client_id] = (client, time.time() + keep)
            return client
        rows = self.db.run("select info from clients where client_id = ?", (client_id,))
        return Client.model_validate_json(rows[0][0]) if rows else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        bad = [str(u) for u in client_info.redirect_uris or [] if not redirect_ok(str(u))]
        if bad or not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri", "redirect URIs must be HTTPS or loopback HTTP")
        self.db.run("insert into clients values (?, ?, ?)", (client_info.client_id, client_info.model_dump_json(), time.time()))

    # -- authorization and consent ---------------------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        resource = params.resource or self.resource
        if canonical(resource) != canonical(self.resource):
            raise AuthorizeError("invalid_target", f"this server issues tokens only for {self.resource}")
        key = self.db.hold(
            "consent",
            {
                "client_id": client.client_id,
                "client_name": client.client_name or "",
                "redirect_uri": str(params.redirect_uri),
                "explicit": params.redirect_uri_provided_explicitly,
                "state": params.state,
                "scopes": params.scopes or [],
                "challenge": params.code_challenge,
                "resource": self.resource,
            },
            CONSENT_S,
        )
        return f"{self.issuer}/oauth/consent?request={key}"

    def consent_request(self, key: str) -> dict | None:
        return self.db.peek("consent", key)

    def decide(self, key: str, user: User | None, approve: bool) -> str | None:
        """Where to send the browser after the consent page: the client's redirect URI with a
        code (or access_denied), the state and this server's issuer. None for an unknown request."""
        req = self.db.take("consent", key)
        if req is None:
            return None
        if not approve or user is None:
            return construct_redirect_uri(req["redirect_uri"], error="access_denied", state=req["state"], iss=self.issuer)
        code = secrets.token_urlsafe(32)
        data = {**req, "iss": user.issuer, "sub": user.sub, "email": user.email}
        self.db.run("insert into codes values (?, ?, ?, 0, ?)", (digest(code), json.dumps(data), time.time() + CODE_S, ""))
        log.info("authorized %s for %s", req["client_id"][:120], user.email)
        return construct_redirect_uri(req["redirect_uri"], code=code, state=req["state"], iss=self.issuer)

    # -- codes and tokens ------------------------------------------------------------------------

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        rows = self.db.run("select data, expires, used, chain from codes where hash = ?", (digest(authorization_code),))
        if not rows:
            return None
        data, expires, used, chain = rows[0]
        if used:  # a replayed code: whatever it gave is suspect
            self.revoke_chain(chain)
            log.warning("authorization code replayed for %s; its tokens are revoked", client.client_id[:120])
            return None
        d = json.loads(data)
        return AuthorizationCode(
            code=authorization_code,
            scopes=d["scopes"],
            expires_at=expires,
            client_id=d["client_id"],
            code_challenge=d["challenge"],
            redirect_uri=d["redirect_uri"],
            redirect_uri_provided_explicitly=d["explicit"],
            resource=d["resource"],
            subject=f"{d['iss']} {d['sub']}",
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        h = digest(authorization_code.code)
        chain = secrets.token_urlsafe(16)
        if self.db.change("update codes set used = 1, chain = ? where hash = ? and used = 0", (chain, h)) != 1:
            raise TokenError("invalid_grant", "authorization code already used")
        d = json.loads(self.db.run("select data from codes where hash = ?", (h,))[0][0])
        user = User(d["iss"], d["sub"], d["email"])
        if not self.allowed(user):
            raise TokenError("invalid_grant", "this account may no longer use this server")
        return self._issue(chain, client.client_id, user, d["resource"], d["scopes"])

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        rows = self.db.run(
            "select chain, client_id, iss, sub, resource, scopes, expires, spent from tokens where hash = ? and kind = 'refresh'",
            (digest(refresh_token),),
        )
        if not rows:
            return None
        chain, client_id, iss, sub, resource, scopes, expires, spent = rows[0]
        if spent:
            self.revoke_chain(chain)
            log.warning("refresh token reused for %s; its chain is revoked", client_id[:120])
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client_id,
            scopes=json.loads(scopes),
            expires_at=int(expires),
            resource=resource,
            subject=f"{iss} {sub}",
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        h = digest(refresh_token.token)
        if self.db.change("update tokens set spent = 1 where hash = ? and spent = 0", (h,)) != 1:
            chain = self.db.run("select chain from tokens where hash = ?", (h,))
            if chain:
                self.revoke_chain(chain[0][0])
            raise TokenError("invalid_grant", "refresh token already used")
        chain, iss, sub, email = self.db.run("select chain, iss, sub, email from tokens where hash = ?", (h,))[0]
        user = User(iss, sub, email)
        if not self.allowed(user):
            self.revoke_chain(chain)
            raise TokenError("invalid_grant", "this account may no longer use this server")
        self.db.run("update tokens set spent = 1 where chain = ? and kind = 'access'", (chain,))  # the old access token goes too
        return self._issue(chain, client.client_id, user, refresh_token.resource or self.resource, scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self._access(token)
        if row is None:
            return None
        client_id, iss, sub, _, resource, scopes, expires = row
        return AccessToken(
            token=token, client_id=client_id, scopes=json.loads(scopes), expires_at=int(expires), resource=resource, subject=f"{iss} {sub}"
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        rows = self.db.run("select chain from tokens where hash = ?", (digest(token.token),))
        if rows:
            self.revoke_chain(rows[0][0])

    def user_for_access_token(self, token: str) -> User | None:
        """The user an access token speaks for, if it is live and was issued for this server's `/mcp`."""
        row = self._access(token)
        if row is None or canonical(row[4]) != canonical(self.resource):
            return None
        return User(row[1], row[2], row[3])

    def _access(self, token: str) -> tuple | None:
        rows = self.db.run(
            "select client_id, iss, sub, email, resource, scopes, expires from tokens where hash = ? and kind = 'access' and spent = 0 and expires > ?",
            (digest(token), time.time()),
        )
        return rows[0] if rows else None

    def _issue(self, chain: str, client_id: str, user: User, resource: str, scopes: list[str]) -> OAuthToken:
        access, refresh, now = f"sta_{secrets.token_urlsafe(32)}", f"str_{secrets.token_urlsafe(32)}", time.time()
        for token, kind, ttl in ((access, "access", ACCESS_S), (refresh, "refresh", REFRESH_S)):
            self.db.run(
                "insert into tokens values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (digest(token), kind, chain, client_id, user.issuer, user.sub, user.email, resource, json.dumps(scopes), now + ttl),
            )
        return OAuthToken(access_token=access, expires_in=ACCESS_S, refresh_token=refresh, scope=" ".join(scopes) or None)

    def revoke_chain(self, chain: str) -> None:
        if chain:
            self.db.run("update tokens set spent = 1 where chain = ?", (chain,))

    def revoke_user(self, user: User) -> None:
        self.db.run("update tokens set spent = 1 where iss = ? and sub = ?", (user.issuer, user.sub))

    # -- metadata --------------------------------------------------------------------------------

    def server_metadata(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "registration_endpoint": f"{self.issuer}/register",
            "revocation_endpoint": f"{self.issuer}/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }

    def resource_metadata(self) -> dict:
        return {
            "resource": self.resource,
            "authorization_servers": [self.issuer],
            "bearer_methods_supported": ["header"],
            "resource_name": "showntell",
        }

    @property
    def resource_metadata_url(self) -> str:
        return f"{self.issuer}/.well-known/oauth-protected-resource/mcp"


def routes(server: AuthServer) -> list:
    """Starlette routes: both metadata documents, then the SDK's authorize, token, register and
    revoke handlers on `server`, the token endpoint behind the `resource` check."""
    from mcp.server.auth.routes import create_auth_routes
    from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
    from pydantic import AnyHttpUrl
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route, request_response

    def document(body: dict):
        async def handle(request):
            return JSONResponse(body, headers={"Cache-Control": "public, max-age=3600"})

        return CORSMiddleware(
            request_response(handle), allow_origins="*", allow_methods=["GET", "OPTIONS"], allow_headers=["mcp-protocol-version"]
        )

    sdk = create_auth_routes(
        server,  # type: ignore[arg-type]
        AnyHttpUrl(server.issuer),
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )
    out = [
        Route("/.well-known/oauth-authorization-server", document(server.server_metadata()), methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource/mcp", document(server.resource_metadata()), methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource", document(server.resource_metadata()), methods=["GET", "OPTIONS"]),
    ]
    for r in sdk:
        if r.path == "/.well-known/oauth-authorization-server":
            continue
        if r.path == "/token":
            r = Route(r.path, ResourceCheck(r.app, server), methods=["POST", "OPTIONS"])
        out.append(r)
    return out


class ResourceCheck:
    """In front of the SDK's token endpoint: a `resource` other than this server's `/mcp` is
    refused with invalid_target (RFC 8707), since the SDK's handler never looks at it."""

    def __init__(self, app, server: AuthServer) -> None:
        self.app = app
        self.server = server

    async def __call__(self, scope, receive, send):
        from urllib.parse import parse_qs

        from starlette.responses import JSONResponse

        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        body, more = b"", True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
            if len(body) > 65536:
                break
        resources = parse_qs(body.decode(errors="replace")).get("resource", [])
        if any(canonical(r) != canonical(self.server.resource) for r in resources):
            r = JSONResponse(
                {"error": "invalid_target", "error_description": f"this server issues tokens only for {self.server.resource}"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
            return await r(scope, receive, send)
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        return await self.app(scope, replay, send)
