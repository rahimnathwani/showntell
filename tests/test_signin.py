"""Hosted sign-in, phase 2 (docs/hosted-design.md "Sign-in"): the provider presets and their
identity and domain rules, browser sessions, and showntell as the authorization server for `/mcp`,
up to a full MCP client flow from discovery to a tool call. All against `fake_oidc.FakeOIDC`."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import re
import secrets
import socket
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
import uvicorn
from fake_oidc import CLIENT_ID, CLIENT_SECRET, FakeOIDC
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from showntell import hosted, oauth
from showntell.app import build_app
from showntell.authdb import AuthDB, DBSessions, digest
from showntell.config import Config
from showntell.identity import Identity, User
from showntell.oidc import Provider, SignInRefused, check_claims
from showntell.service import LocalService
from showntell.signin import OIDCLogin, local_path
from showntell.store import Store

TENANT = "8f2f3c1e-2b0a-4c43-9d55-1a2b3c4d5e6f"
GOOGLE = {"sub": "g-1", "email": "ann@example.com", "email_verified": True, "hd": "example.com"}
ENTRA = {"sub": "e-1", "tid": TENANT, "preferred_username": "ann@example.com", "email": "ann@example.com"}
GENERIC = {"sub": "o-1", "email": "ann@example.com", "email_verified": True}
REDIRECT = "http://localhost:53682/callback"


def port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def signin_server(tmp_path, narrator, preset="generic", domains=("example.com",), tenant="", fetch=None):
    fake = FakeOIDC(port(), f"/{TENANT}/v2.0" if preset == "entra" else "")
    sport = port()
    base = f"http://localhost:{sport}"
    async with fake.running():
        cfg = Config(
            host="127.0.0.1",
            port=sport,
            data_dir=tmp_path / "data",
            public_url=base,
            mode="hosted",
            oidc=preset,
            oidc_client_id=CLIENT_ID,
            oidc_client_secret=CLIENT_SECRET,
            oidc_issuer=fake.issuer,
            entra_tenant=tenant or TENANT,
            allowed_domains=",".join(domains),
            dev_users="",
            access_token="",
            deliver="link",
        )
        hosted.check_config(cfg)
        db = AuthDB(cfg.data_dir / "auth.db")
        provider = Provider(preset, CLIENT_ID, CLIENT_SECRET, fake.issuer, base + "/oidc/callback", list(domains), cfg.entra_tenant)
        login = OIDCLogin(provider, db, base, fetch_metadata=fetch)
        h = hosted.Hosted(
            cfg.limits, Identity(login, sessions=DBSessions(db), secure_cookie=False), f"http://127.0.0.1:{sport}", base, list(domains)
        )
        store = Store(cfg.data_dir, base, narrator, hosted=h)
        store.loop = asyncio.get_running_loop()
        service = LocalService(store)
        app = build_app(cfg, service, "rpc-token", time.time())
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=sport, log_level="warning", lifespan="on"))
        task = asyncio.create_task(server.serve())
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        try:
            yield SimpleNamespace(fake=fake, login=login, auth=login.auth, db=db, store=store, url=base, provider=provider, hosted=h)
        finally:
            server.should_exit = True
            await task


@pytest.fixture
async def srv(tmp_path, narrator):
    async with signin_server(tmp_path, narrator) as s:
        yield s


@contextlib.asynccontextmanager
async def browser(srv, claims: dict | None = None):
    """A browser; with `claims`, signed in as them through the fake provider."""
    async with httpx.AsyncClient(base_url=srv.url, follow_redirects=True, timeout=10) as c:
        if claims is not None:
            srv.fake.claims = claims
            r = await c.get("/login?next=/")
            assert r.status_code == 200 and str(r.url).rstrip("/") == srv.url, (r.status_code, r.text[:300])
        yield c


async def csrf_of(c, path: str = "/") -> str:
    return re.search(r'data-csrf="([^"]+)"', (await c.get(path)).text).group(1)


# -- configuration ------------------------------------------------------------------------------------


def base_cfg(tmp_path, **kw) -> Config:
    d = dict(
        host="0.0.0.0",
        port=4747,
        data_dir=tmp_path,
        public_url="https://showntell.example.com",
        mode="hosted",
        oidc="google",
        oidc_client_id="id",
        oidc_client_secret="secret",
        allowed_domains="example.com",
        entra_tenant=TENANT,
        oidc_issuer="",
        dev_users="",
        deliver="link",
    )
    return Config(**{**d, **kw})


@pytest.mark.parametrize(
    "change, says",
    [
        ({"oidc": "okta"}, "must be one of"),
        ({"oidc_client_secret": ""}, "CLIENT_ID and SHOWNTELL_OIDC_CLIENT_SECRET"),
        ({"allowed_domains": ""}, "needs SHOWNTELL_ALLOWED_DOMAINS"),
        ({"oidc": "generic", "allowed_domains": "", "oidc_issuer": "https://id.example.com"}, "needs SHOWNTELL_ALLOWED_DOMAINS"),
        ({"oidc": "entra", "entra_tenant": "example.com"}, "a GUID, not a domain"),
        ({"oidc": "generic", "oidc_issuer": ""}, "needs SHOWNTELL_OIDC_ISSUER"),
        ({"oidc": "generic", "oidc_issuer": "http://id.example.com"}, "needs SHOWNTELL_OIDC_ISSUER"),
        ({"dev_users": "a@example.com"}, "not both"),
        ({"oidc": ""}, "hosted mode needs sign-in"),
    ],
)
def test_sign_in_settings_are_checked_at_start(tmp_path, change, says):
    with pytest.raises(SystemExit, match=says):
        hosted.check_config(base_cfg(tmp_path, **change))


def test_good_settings_start(tmp_path):
    hosted.check_config(base_cfg(tmp_path))
    hosted.check_config(base_cfg(tmp_path, oidc="entra", allowed_domains=""))  # anyone in the tenant
    hosted.check_config(base_cfg(tmp_path, oidc="generic", oidc_issuer="https://id.example.com"))
    h = hosted.build(base_cfg(tmp_path))
    assert isinstance(h.identity.login, OIDCLogin) and h.identity.login.provider.issuer == "https://accounts.google.com"
    assert (tmp_path / "auth.db").stat().st_mode & 0o077 == 0


# -- the rules for who may sign in --------------------------------------------------------------------


@pytest.mark.parametrize(
    "preset, claims, domains, says",
    [
        ("google", {**GOOGLE, "hd": "other.org"}, ["example.com"], "other.org cannot use"),
        ("google", {k: v for k, v in GOOGLE.items() if k != "hd"}, ["example.com"], "not a personal one"),
        ("google", {**GOOGLE, "email": "ann@gmail.com", "hd": "example.com"}, ["example.com"], "not verified for your company"),
        ("google", {**GOOGLE, "email_verified": False}, ["example.com"], "not verified"),
        ("entra", {**ENTRA, "tid": "00000000-0000-0000-0000-000000000000"}, [], "another organisation"),
        ("entra", {**ENTRA, "preferred_username": "ann@contractor.net"}, ["example.com"], "contractor.net cannot use"),
        # The editable `email` claim never gets someone in.
        ("entra", {**ENTRA, "preferred_username": "mallory@contractor.net", "email": "ceo@example.com"}, ["example.com"], "cannot use"),
        ("generic", {**GENERIC, "email_verified": False}, ["example.com"], "not verified"),
        ("generic", {**GENERIC, "email_verified": "true"}, ["example.com"], "not verified"),
        ("generic", {**GENERIC, "email": "ann@other.org"}, ["example.com"], "other.org cannot use"),
    ],
)
def test_the_preset_rules_refuse(preset, claims, domains, says):
    with pytest.raises(SignInRefused, match=says):
        check_claims(preset, claims, domains, TENANT)


def test_the_preset_rules_accept_and_name_the_user():
    assert check_claims("google", GOOGLE, ["example.com"]) == "ann@example.com"
    assert check_claims("entra", {**ENTRA, "email": "someone-else@example.com"}, [], TENANT) == "ann@example.com"
    assert check_claims("entra", ENTRA, ["Example.com"], TENANT.upper()) == "ann@example.com"
    assert check_claims("generic", {**GENERIC, "email": "Ann@Example.com"}, ["example.com"]) == "ann@example.com"


@pytest.mark.parametrize(
    "preset, claims, refused",
    [
        ("google", GOOGLE, False),
        ("google", {**GOOGLE, "hd": "other.org", "email": "ann@other.org"}, True),
        ("google", {k: v for k, v in GOOGLE.items() if k != "hd"}, True),
        ("entra", ENTRA, False),
        ("entra", {**ENTRA, "tid": "00000000-0000-0000-0000-000000000000"}, True),
        ("entra", {**ENTRA, "preferred_username": "ann@contractor.net"}, True),
        ("generic", GENERIC, False),
        ("generic", {**GENERIC, "email_verified": False}, True),
    ],
)
async def test_sign_in_through_the_provider(tmp_path, narrator, preset, claims, refused):
    async with signin_server(tmp_path, narrator, preset=preset) as s, browser(s) as c:
        s.fake.claims = claims
        r = await c.get("/login?next=/")
        if refused:
            assert r.status_code == 403 and "You cannot use this server" in r.text
            assert "showntell_session" not in c.cookies
            assert (await c.get("/", follow_redirects=False)).status_code == 303  # still sent to sign in
        else:
            assert r.status_code == 200 and "showntell" in r.text
            assert s.db.claims(s.provider.issuer, claims["sub"])["sub"] == claims["sub"]


@pytest.mark.parametrize(
    "tamper, why",
    [
        ({"_key": "other"}, "signed with a key not in the JWKS"),
        ({"aud": "someone-else"}, "for another app"),
        ({"iss": "https://evil.example"}, "from another issuer"),
        ({"exp": int(time.time()) - 3600}, "expired"),
        ({"nonce": "replayed"}, "with another sign-in's nonce"),
    ],
)
async def test_id_tokens_that_do_not_check_out_are_refused(srv, tamper, why):
    srv.fake.tamper = tamper
    async with browser(srv) as c:
        srv.fake.claims = GENERIC
        r = await c.get("/login?next=/")
    assert r.status_code == 403 and "did not check out" in r.text, why


async def test_a_callback_from_another_browser_is_refused(srv):
    async with browser(srv) as a, browser(srv) as b:
        srv.fake.claims = GENERIC
        r = await a.get("/login?next=/", follow_redirects=False)
        to_provider = await a.get(r.headers["location"], follow_redirects=False)
        callback = to_provider.headers["location"]
        r = await b.get(callback)  # the victim's browser never started this sign-in
        assert r.status_code == 400 and "another browser" in r.text
        r = await a.get(callback)  # the browser that started it still finishes
        assert r.status_code == 200


def test_after_sign_in_only_local_paths():
    assert local_path("/w/abc/?t=1") == "/w/abc/?t=1"
    for bad in ("//evil.example/", "https://evil.example/", "/\\evil.example", "", "evil"):
        assert local_path(bad) == "/"


async def test_pages_send_a_signed_out_browser_to_sign_in(srv):
    async with browser(srv) as c:
        for path in ("/", "/w/" + "a" * 22 + "/"):
            r = await c.get(path, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
        assert (await c.get("/w/" + "a" * 22 + "/data.json", follow_redirects=False)).status_code == 401


async def test_sessions_survive_a_restart_and_sign_out_ends_them(srv):
    async with browser(srv, GENERIC) as c:
        sid = c.cookies["showntell_session"]
        assert srv.db.run("select count(*) from sessions where hash = ?", (digest(sid),))[0][0] == 1
        assert DBSessions(srv.db).get(sid).user.email == "ann@example.com"  # a fresh process reads it back
        assert srv.db.run("select count(*) from sessions where hash = ?", (sid,))[0][0] == 0  # never stored in the clear
        csrf = await csrf_of(c)
        r = await c.post("/logout", json={}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert (await c.get("/", follow_redirects=False)).status_code == 303


async def test_sign_out_in_the_gallery_stays_signed_out(srv):
    # The provider still has its own session, so going on to /login would sign straight back in.
    pw = pytest.importorskip("playwright.async_api")
    srv.fake.claims = GENERIC
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"Chromium unavailable: {e}")
        try:
            page = await b.new_page()
            await page.goto(f"{srv.url}/login?next=/")
            await page.click(".signout")
            await page.wait_for_selector("text=Signed out of showntell")
            await page.wait_for_timeout(500)
            assert page.url.rstrip("/") == srv.url
            assert srv.db.run("select count(*) from sessions")[0][0] == 0
            await page.click("text=Sign in again")
            await page.wait_for_selector(".signout")  # and back in on purpose
        finally:
            await b.close()


async def test_session_cookie_flags_and_csrf(tmp_path, narrator):
    async with signin_server(tmp_path, narrator) as s:
        s.hosted.identity.secure_cookie = True
        s.login.secure = True
        async with browser(s) as c:
            r = await c.get("/login?next=/", follow_redirects=False)
            assert all(f in r.headers["set-cookie"] for f in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/oidc"))
        session = s.hosted.identity.cookie(s.hosted.identity.sessions.create(User("i", "s", "ann@example.com")))
        assert all(f in session for f in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/", "Max-Age=4319"))
        async with browser(s) as c:  # a POST without a session or CSRF token
            assert (await c.post("/logout", json={})).status_code in (401, 403)


# -- MCP authorization: discovery ---------------------------------------------------------------------


async def test_mcp_without_a_token_points_at_the_metadata(srv):
    async with httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.post("/mcp", json={})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == f'Bearer resource_metadata="{srv.url}/.well-known/oauth-protected-resource/mcp"'
        prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert prm == (await c.get("/.well-known/oauth-protected-resource")).json()
        assert prm["resource"] == f"{srv.url}/mcp" and prm["authorization_servers"] == [srv.url]
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
    assert meta["issuer"] == srv.url and meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["client_id_metadata_document_supported"] is True and meta["authorization_response_iss_parameter_supported"] is True
    assert meta["registration_endpoint"] == f"{srv.url}/register" and "none" in meta["token_endpoint_auth_methods_supported"]


async def test_a_provider_id_token_is_refused_at_mcp(srv):
    """No token passthrough: the provider's own tokens are worthless here."""
    async with browser(srv, GENERIC):
        pass
    async with httpx.AsyncClient(base_url=srv.url) as c:
        for token in (srv.fake.issued[-1], "provider-access-token"):
            r = await c.post("/mcp", json={}, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401


# -- MCP authorization: the flow by hand ---------------------------------------------------------------


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


async def register(c, redirect=REDIRECT) -> str:
    r = await c.post(
        "/register",
        json={
            "client_name": "Hand-made client",
            "redirect_uris": [redirect],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


async def authorize(c, client_id: str, challenge: str, approve: bool = True, **extra) -> dict:
    """Through /authorize and the consent page; the query the app's redirect URI receives."""
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-1",
        "resource": str(c.base_url).rstrip("/") + "/mcp",
    }
    q.update(extra)
    r = await c.get("/authorize?" + urlencode(q))
    assert r.status_code == 200 and "Connect" in r.text, r.text[:300]
    key = parse_qs(urlparse(str(r.url)).query)["request"][0]
    csrf = re.search(r'data-csrf="([^"]+)"', r.text).group(1)
    d = await c.post("/oauth/consent", json={"request": key, "approve": approve}, headers={"X-CSRF-Token": csrf})
    back = urlparse(d.json()["redirect"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    return {k: v[0] for k, v in parse_qs(back.query).items()}


async def tokens(c, **form) -> httpx.Response:
    return await c.post("/token", data=form)


async def signed_in_tokens(srv, c) -> tuple[str, dict]:
    cid = await register(c)
    verifier, challenge = pkce()
    got = await authorize(c, cid, challenge)
    assert got["state"] == "st-1" and got["iss"] == srv.url
    r = await tokens(
        c,
        grant_type="authorization_code",
        code=got["code"],
        redirect_uri=REDIRECT,
        client_id=cid,
        code_verifier=verifier,
        resource=f"{srv.url}/mcp",
    )
    assert r.status_code == 200, r.text
    return cid, r.json()


async def mcp_status(srv, token: str) -> int:
    async with httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            },
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
        )
    return r.status_code


async def test_tokens_are_opaque_hashed_and_short_lived(srv):
    async with browser(srv, GENERIC) as c:
        _, t = await signed_in_tokens(srv, c)
    assert t["expires_in"] == 3600 and t["token_type"] == "Bearer" and t["refresh_token"]
    assert "." not in t["access_token"]  # not a JWT
    rows = srv.db.run("select hash, kind, expires, resource from tokens")
    assert {r[1] for r in rows} == {"access", "refresh"} and all(r[0] not in (t["access_token"], t["refresh_token"]) for r in rows)
    assert {r[0] for r in rows} == {digest(t["access_token"]), digest(t["refresh_token"])}
    refresh_left = next(r[2] for r in rows if r[1] == "refresh") - time.time()
    assert 29.9 * 86400 < refresh_left <= 30 * 86400 and all(r[3] == f"{srv.url}/mcp" for r in rows)
    assert await mcp_status(srv, t["access_token"]) == 200


async def test_expired_and_wrong_audience_tokens_are_refused(srv):
    async with browser(srv, GENERIC) as c:
        _, t = await signed_in_tokens(srv, c)
    srv.db.run("update tokens set expires = ? where hash = ?", (time.time() - 1, digest(t["access_token"])))
    assert await mcp_status(srv, t["access_token"]) == 401
    async with browser(srv, GENERIC) as c:
        _, t = await signed_in_tokens(srv, c)
    srv.db.run("update tokens set resource = ? where hash = ?", ("https://other.example/mcp", digest(t["access_token"])))
    assert await mcp_status(srv, t["access_token"]) == 401


async def test_refresh_rotates_and_reuse_revokes_the_chain(srv):
    async with browser(srv, GENERIC) as c:
        cid, first = await signed_in_tokens(srv, c)
        r = await tokens(c, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=cid, resource=f"{srv.url}/mcp")
        assert r.status_code == 200, r.text
        second = r.json()
        assert second["refresh_token"] != first["refresh_token"]
        assert await mcp_status(srv, first["access_token"]) == 401  # replaced
        assert await mcp_status(srv, second["access_token"]) == 200
        r = await tokens(c, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=cid)
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        # The spent token came back: someone else has it, so the whole chain goes.
        assert await mcp_status(srv, second["access_token"]) == 401
        r = await tokens(c, grant_type="refresh_token", refresh_token=second["refresh_token"], client_id=cid)
        assert r.status_code == 400


async def test_a_removed_domain_stops_the_refresh(srv):
    async with browser(srv, GENERIC) as c:
        cid, t = await signed_in_tokens(srv, c)
        srv.provider.domains = ["example.org"]  # the admin drops example.com and restarts
        r = await tokens(c, grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=cid)
        assert r.status_code == 400 and "no longer" in r.json()["error_description"]
        assert await mcp_status(srv, t["access_token"]) == 401  # the chain is revoked with it


async def test_codes_need_pkce_and_work_once(srv):
    async with browser(srv, GENERIC) as c:
        cid = await register(c)
        verifier, challenge = pkce()
        got = await authorize(c, cid, challenge)
        r = await tokens(
            c, grant_type="authorization_code", code=got["code"], redirect_uri=REDIRECT, client_id=cid, code_verifier="wrong" * 10
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        r = await tokens(c, grant_type="authorization_code", code=got["code"], redirect_uri=REDIRECT, client_id=cid, code_verifier=verifier)
        assert r.status_code == 200
        access = r.json()["access_token"]
        r = await tokens(c, grant_type="authorization_code", code=got["code"], redirect_uri=REDIRECT, client_id=cid, code_verifier=verifier)
        assert r.status_code == 400
        assert await mcp_status(srv, access) == 401  # a replayed code revokes what it gave
        q = {
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "plain",
        }
        r = await c.get("/authorize?" + urlencode(q), follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith(REDIRECT + "?error=invalid_request")


async def test_the_resource_must_be_this_server(srv):
    async with browser(srv, GENERIC) as c:
        cid = await register(c)
        verifier, challenge = pkce()
        q = {
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "state": "s",
            "resource": "https://other.example/mcp",
        }
        r = await c.get("/authorize?" + urlencode(q), follow_redirects=False)
        assert r.status_code == 302 and "error=invalid_target" in r.headers["location"]
        got = await authorize(c, cid, challenge)
        r = await tokens(
            c,
            grant_type="authorization_code",
            code=got["code"],
            redirect_uri=REDIRECT,
            client_id=cid,
            code_verifier=verifier,
            resource="https://other.example/mcp",
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid_target"
        loud = srv.url.replace("localhost", "LOCALHOST") + "/mcp/"
        got = await authorize(c, cid, challenge, resource=loud)  # host case and a trailing slash are forgiven
        assert "code" in got


async def test_consent_names_the_app_and_can_be_refused(srv):
    async with browser(srv, GENERIC) as c:
        cid = await register(c)
        _, challenge = pkce()
        r = await c.get(
            "/authorize?"
            + urlencode({"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "code_challenge": challenge, "state": "s"})
        )
        assert (
            "Hand-made client" in r.text
            and "http://localhost:53682" in r.text
            and "your own computer" in r.text
            and "ann@example.com" in r.text
        )
        got = await authorize(c, cid, challenge, approve=False)
        assert got == {"error": "access_denied", "state": "st-1", "iss": srv.url}
        r = await c.post("/oauth/consent", json={"request": "made-up", "approve": True}, headers={"X-CSRF-Token": await csrf_of(c)})
        assert r.status_code == 404
    async with browser(srv) as c:  # signed out: sent to sign in, then back to the consent page
        r = await c.get("/oauth/consent?request=x", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Foauth%2Fconsent%3Frequest%3Dx"


async def test_registration_and_redirect_uris_are_checked(srv):
    async with httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.post(
            "/register", json={"client_name": "x", "redirect_uris": ["http://evil.example/cb"], "token_endpoint_auth_method": "none"}
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
        cid = await register(c)
        _, challenge = pkce()
        r = await c.get(
            "/authorize?"
            + urlencode(
                {"response_type": "code", "client_id": cid, "redirect_uri": "http://localhost:1/other", "code_challenge": challenge}
            )
        )
        assert r.status_code == 400  # shown, never redirected to


@pytest.mark.parametrize(
    "given, ok",
    [
        ("http://localhost:53682/callback", True),
        ("http://localhost:61000/callback", True),  # a native client's port changes per sign-in
        ("http://localhost/callback", True),
        ("http://127.0.0.1:61000/callback", False),  # another host, even another loopback name
        ("http://localhost:61000/other", False),
        ("http://localhost:61000/callback?x=1", False),
        ("https://localhost:61000/callback", False),
        ("http://user@localhost:61000/callback", False),
        ("http://localhost.evil.example:61000/callback", False),
    ],
)
def test_loopback_redirects_match_on_any_port(given, ok):
    assert oauth.same_redirect(REDIRECT, given) is ok
    assert oauth.same_redirect("https://app.example/cb", "https://app.example:8443/cb") is False  # only loopback


async def test_a_registered_loopback_client_signs_in_on_a_new_port(srv):
    async with httpx.AsyncClient(base_url=srv.url) as c:
        cid = await register(c)
        _, challenge = pkce()
        q = {"response_type": "code", "client_id": cid, "redirect_uri": "http://localhost:61000/callback", "code_challenge": challenge}
        q |= {"code_challenge_method": "S256", "state": "s", "resource": srv.url.rstrip("/") + "/mcp"}
        r = await c.get("/authorize?" + urlencode(q))
        assert r.status_code == 302 and "localhost:61000" not in r.headers["location"]  # past the check, on to sign-in
        q["redirect_uri"] = "http://localhost:61000/other"
        assert (await c.get("/authorize?" + urlencode(q))).status_code == 400


# -- client ID metadata documents ---------------------------------------------------------------------

CIMD_URL = "https://client.example/oauth/metadata.json"


def document(**over) -> dict:
    return {
        "client_id": CIMD_URL,
        "client_name": "Metadata client",
        "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "none",
        **over,
    }


@pytest.mark.parametrize(
    "doc, ok",
    [
        (document(), True),
        (document(client_id="https://client.example/other.json"), False),
        (document(redirect_uris=["http://evil.example/cb"]), False),
        (document(redirect_uris=[]), False),
        (document(client_name=""), False),
        (document(token_endpoint_auth_method="private_key_jwt"), False),
        ("not an object", False),
    ],
)
async def test_client_metadata_documents_are_checked(tmp_path, doc, ok):
    async def fetch(url):
        return doc, 300

    server = oauth.AuthServer(AuthDB(tmp_path / "a.db"), "http://localhost:1", lambda u: True, fetch=fetch)
    client = await server.get_client(CIMD_URL)
    assert (client is not None) == ok
    if ok:
        assert client.client_name == "Metadata client" and client.token_endpoint_auth_method == "none"


async def test_client_metadata_is_never_fetched_from_private_addresses():
    for url in ("https://127.0.0.1/c.json", "https://localhost/c.json", "https://10.0.0.8/c.json", "https://169.254.169.254/latest"):
        with pytest.raises(ValueError, match="not a public address"):
            await oauth.fetch_client_metadata(url)
    assert not oauth.is_metadata_url("https://client.example/") and not oauth.is_metadata_url("http://client.example/c.json")


async def test_client_metadata_documents_are_cached(tmp_path):
    calls = []

    async def fetch(url):
        calls.append(url)
        return document(), 300

    server = oauth.AuthServer(AuthDB(tmp_path / "a.db"), "http://localhost:1", lambda u: True, fetch=fetch)
    await server.get_client(CIMD_URL)
    await server.get_client(CIMD_URL)
    assert calls == [CIMD_URL]


# -- the whole MCP flow, as a real client does it ------------------------------------------------------


class Memory:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info):
        self.client = client_info


def sdk_client(srv, user_browser, storage: Memory, metadata_url: str | None = None) -> Client:
    """The MCP SDK's own OAuth client against showntell; the 'browser' approves on the consent page."""
    result: dict = {}

    async def open_browser(url: str) -> None:
        r = await user_browser.get(url)
        assert "/oauth/consent" in str(r.url), r.text[:300]
        key = parse_qs(urlparse(str(r.url)).query)["request"][0]
        csrf = re.search(r'data-csrf="([^"]+)"', r.text).group(1)
        d = await user_browser.post("/oauth/consent", json={"request": key, "approve": True}, headers={"X-CSRF-Token": csrf})
        result.update({k: v[0] for k, v in parse_qs(urlparse(d.json()["redirect"]).query).items()})

    async def callback() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code=result["code"], state=result.get("state"), iss=result.get("iss"))

    meta = OAuthClientMetadata(
        client_name="SDK client",
        redirect_uris=[REDIRECT],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    auth = OAuthClientProvider(f"{srv.url}/mcp", meta, storage, open_browser, callback, client_metadata_url=metadata_url)
    return Client(streamable_http_client(f"{srv.url}/mcp", http_client=create_mcp_http_client(auth=auth)))


async def test_full_mcp_flow_with_dynamic_registration(srv):
    storage = Memory()
    async with browser(srv, GENERIC) as b:
        async with sdk_client(srv, b, storage) as client:
            r = await client.call_tool("start_walkthrough", {"title": "Signed in"})
            assert not r.is_error, r.content
            rows = (await client.call_tool("list_walkthroughs", {})).structured_content["walkthroughs"]
    assert [(x["title"], x["role"]) for x in rows] == [("Signed in", "yours")]
    s = srv.store.get(rows[0]["id"])
    assert s.w.owner == {"iss": srv.fake.issuer, "sub": "o-1", "email": "ann@example.com"}
    assert storage.client.client_id and not oauth.is_metadata_url(storage.client.client_id)
    assert storage.tokens.access_token.startswith("sta_")


async def test_full_mcp_flow_with_a_client_metadata_document(tmp_path, narrator):
    fetched = []

    async def fetch(url):
        fetched.append(url)
        return document(client_name="SDK client"), 300

    async with signin_server(tmp_path, narrator, fetch=fetch) as s:
        storage = Memory()
        async with browser(s, GENERIC) as b, sdk_client(s, b, storage, metadata_url=CIMD_URL) as client:
            r = await client.call_tool("list_walkthroughs", {})
            assert not r.is_error, r.content
    assert fetched and fetched[0] == CIMD_URL
    assert s.db.run("select count(*) from clients")[0][0] == 0  # nothing registered
    assert s.db.run("select distinct client_id from tokens")[0][0] == CIMD_URL


async def test_two_signed_in_users_see_only_their_own(srv):
    async with browser(srv, GENERIC) as ann_b, browser(srv, {**GENERIC, "sub": "o-2", "email": "bob@example.com"}) as bob_b:
        async with sdk_client(srv, ann_b, Memory()) as ann, sdk_client(srv, bob_b, Memory()) as bob:
            handle = (await ann.call_tool("start_walkthrough", {"title": "Ann's"})).structured_content["handle"]
            r = await bob.call_tool("add_slides", {"handle": handle, "markdown": "# x\n\nHello.\n"})
            assert r.is_error and "unknown handle" in json.dumps([c.model_dump() for c in r.content])
            wid = handle.split(".")[0]
            assert (await bob_b.get(f"/w/{wid}/data.json")).status_code == 404
            await ann.call_tool("share_walkthrough", {"handle": handle, "add": ["bob@example.com"]})
            assert (await bob_b.get(f"/w/{wid}/data.json")).status_code == 200


def test_entra_issuer_and_tenant():
    from showntell.oidc import issuer_for

    assert issuer_for("entra", TENANT) == f"https://login.microsoftonline.com/{TENANT}/v2.0"
    assert issuer_for("generic", issuer="https://id.example.com/") == "https://id.example.com"


async def test_the_discovery_document_must_name_the_issuer():
    doc = {"issuer": "https://evil.example", "authorization_endpoint": "x", "token_endpoint": "y", "jwks_uri": "z"}
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=doc)))
    p = Provider(
        "generic", CLIENT_ID, CLIENT_SECRET, "https://id.example.com", "https://s.example/oidc/callback", ["example.com"], http=http
    )
    with pytest.raises(SignInRefused, match="names issuer"):
        await p.metadata()


def test_users_key_is_issuer_and_subject():
    u = User("https://accounts.google.com", "123", "ann@example.com")
    assert u.key == ("https://accounts.google.com", "123")


async def test_client_registrations_are_capped(srv):
    async with httpx.AsyncClient(base_url=srv.url) as c:
        body = {"client_name": "x", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}
        codes = [(await c.post("/register", json=body)).status_code for _ in range(hosted.REGISTRATIONS_PER_MIN + 1)]
    assert codes[:-1] == [201] * hosted.REGISTRATIONS_PER_MIN and codes[-1] == 429
