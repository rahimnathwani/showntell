"""Hosted mode, phase 1 (docs/hosted-design.md): startup checks, the stub login, two users and
their shares, no disk or network from slide content, headers and CSRF, and fair use.

Each test runs a real server (uvicorn on a free port, in the test's loop) with three stub users,
and talks to it as an MCP client over HTTP and as a browser with a session cookie.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from showntell import hosted
from showntell.app import build_app
from showntell.config import Config, Limits
from showntell.identity import SESSION_COOKIE, Identity, StubLogin, User
from showntell.service import LocalService
from showntell.speech.narrator import Narrator
from showntell.store import Store

TOKENS = {"alice@example.com": "tok-alice", "bob@example.com": "tok-bob", "carol@example.com": "tok-carol"}
OPENING = "# How caching works\n\nEvery request checks a small cache first.\n"
BODY = (
    "## The lookup {#lookup}\n\n```python start=10\ndef get(self, key):\n    return self._data[key]\n```\n\nIt [returns](#L11) the value.\n"
)


def hosted_cfg(port: int, data_dir, **kw) -> Config:
    base = dict(
        host="127.0.0.1",
        port=port,
        data_dir=data_dir,
        public_url=f"http://localhost:{port}",
        mode="hosted",
        dev_users=",".join(TOKENS),
        allowed_domains="example.com",
        access_token="",
        deliver="link",
    )
    return Config(**{**base, **kw})


@contextlib.asynccontextmanager
async def running(tmp_path, port: int, narrator: Narrator, limits: Limits | None = None, layout_check: bool = False):
    cfg = hosted_cfg(port, tmp_path / "data")
    if limits is not None:
        cfg.limits = limits
    hosted.check_config(cfg)
    h = hosted.Hosted(
        cfg.limits, Identity(StubLogin(TOKENS), secure_cookie=False), f"http://127.0.0.1:{port}", cfg.base_url, ["example.com"]
    )
    store = Store(cfg.data_dir, cfg.base_url, narrator, hosted=h)
    store.loop = asyncio.get_running_loop()
    service = LocalService(store, layout_check=layout_check)
    app = build_app(cfg, service, "rpc-token", time.time())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    try:
        yield SimpleNamespace(cfg=cfg, store=store, service=service, hosted=h, url=cfg.base_url)
    finally:
        server.should_exit = True
        await task


@pytest.fixture
async def srv(tmp_path, narrator, free_port):
    async with running(tmp_path, free_port, narrator) as s:
        yield s


def mcp_as(srv, email: str) -> Client:
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKENS[email]}"})
    return Client(streamable_http_client(f"{srv.url}/mcp", http_client=http))


@contextlib.asynccontextmanager
async def browser_as(srv, email: str):
    """`async with browser_as(srv, email) as (c, csrf)`: a client holding the session cookie, and the page's CSRF token."""
    async with httpx.AsyncClient(base_url=srv.url, timeout=10, follow_redirects=True) as c:
        r = await c.get(f"/dev/login?token={TOKENS[email]}")
        assert r.status_code == 200, r.text  # redirected to the gallery
        yield c, re.search(r'data-csrf="([^"]+)"', r.text).group(1)


def text(result) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


async def start(client, **kw) -> dict:
    r = await client.call_tool("start_walkthrough", {"title": "Caching", "opening": OPENING, **kw})
    assert not r.is_error, text(r)
    return r.structured_content


def wid_of(handle: str) -> str:
    return handle.split(".", 1)[0]


# -- startup ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change, says",
    [
        ({"public_url": ""}, "needs SHOWNTELL_PUBLIC_URL"),
        ({"public_url": "http://showntell.example.com"}, "must be https://"),
        ({"public_url": "https://showntell.example.com/app"}, "must be an origin"),
        ({"public_url": "ftp://localhost"}, "must be an origin"),
        ({"dev_users": ""}, "hosted mode needs sign-in"),
        ({"host": "0.0.0.0"}, "only with a loopback"),
        ({"public_url": "https://showntell.example.com"}, "only with a loopback"),
        ({"deliver": "file"}, "delivers links"),
    ],
)
def test_hosted_mode_refuses_to_start_on_unsafe_settings(tmp_path, change, says):
    with pytest.raises(SystemExit, match=says):
        hosted.check_config(hosted_cfg(4799, tmp_path, **change))


def test_the_dev_login_never_runs_beside_provider_sign_in(tmp_path):
    with pytest.raises(SystemExit, match="not both"):
        hosted.check_config(hosted_cfg(4799, tmp_path, oidc="google", oidc_client_id="i", oidc_client_secret="s"))


def test_a_trial_on_localhost_starts(tmp_path):
    hosted.check_config(hosted_cfg(4799, tmp_path))
    hosted.check_config(hosted_cfg(4799, tmp_path, public_url="http://127.0.0.1:4799"))


def test_the_dev_login_is_announced_and_its_tokens_persist(tmp_path, caplog):
    cfg = hosted_cfg(4799, tmp_path)
    with caplog.at_level("WARNING"):
        first = hosted.build(cfg)
    assert "DEV LOGIN IS ON" in caplog.text and "Never use it in production" in caplog.text
    tokens = first.identity.login.tokens()
    assert set(tokens) == set(TOKENS)
    assert hosted.build(cfg).identity.login.tokens() == tokens
    assert (tmp_path / "dev-logins.json").stat().st_mode & 0o077 == 0


def test_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("SHOWNTELL_QUOTA_SLIDES", "12")
    monkeypatch.setenv("SHOWNTELL_QUOTA_NARRATION_MIN", "2.5")
    lim = Limits()
    assert lim.slides == 12 and lim.narration_min == 2.5 and lim.writing == 3 and lim.browsers == 2
    monkeypatch.setenv("SHOWNTELL_QUOTA_SLIDES", "many")
    with pytest.raises(SystemExit, match="not a number"):
        Limits()


def test_the_shim_refuses_hosted_mode(monkeypatch):
    from showntell.cli import main

    monkeypatch.setenv("SHOWNTELL_MODE", "hosted")
    with pytest.raises(SystemExit, match="over HTTP"):
        main(["mcp"])


# -- sign-in ------------------------------------------------------------------------------------------


async def test_mcp_needs_a_known_bearer_token(srv):
    async with httpx.AsyncClient(base_url=srv.url) as c:
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic tok-alice"}):
            r = await c.post("/mcp", json={}, headers=headers)
            assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")


async def test_a_session_cookie_does_not_work_on_mcp(srv):
    async with browser_as(srv, "alice@example.com") as (c, _):
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401


async def test_pages_need_a_session(srv):
    async with httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/")).status_code == 401
        assert (await c.get("/w/" + "a" * 22 + "/data.json")).status_code == 401
        assert (await c.get("/dev/login?token=wrong")).status_code == 401
        assert (await c.get("/static/player.css")).status_code == 200
        assert (await c.get("/health")).status_code == 200


async def test_unknown_hosts_are_refused(srv):
    async with httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/health", headers={"Host": "evil.example"})).status_code == 421


def test_identity_resolves_tokens_and_cookies():
    ident = Identity(StubLogin({"a@example.com": "t1"}), secure_cookie=True)
    assert ident.from_bearer({"authorization": "Bearer t1"}) == User("showntell-dev-login", "a@example.com", "a@example.com")
    assert ident.from_bearer({"authorization": "Bearer t2"}) is None
    s = ident.sessions.create(ident.from_bearer({"authorization": "bearer t1"}))
    cookie = ident.cookie(s)
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie and "Max-Age=4319" in cookie
    assert ident.from_cookie({"cookie": f"{SESSION_COOKIE}={s.id}"}).user.email == "a@example.com"
    assert ident.from_cookie({"cookie": f"{SESSION_COOKIE}=forged"}) is None
    s.expires = time.time() - 1
    assert ident.from_cookie({"cookie": f"{SESSION_COOKIE}={s.id}"}) is None


# -- two users ----------------------------------------------------------------------------------------

WRITE_TOOLS = [
    ("add_slides", {"markdown": BODY}),
    ("revise_slide", {"slide": "lookup", "markdown": "It changed."}),
    ("finish_walkthrough", {}),
    ("export_walkthrough", {"format": "html"}),
    ("preview_slides", {}),
    ("share_walkthrough", {"add": ["bob@example.com"]}),
]


async def b_is_locked_out(srv, handle: str, bob: Client) -> None:
    """Bob gets "unknown handle" from every tool and 404 from every route for Alice's walkthrough."""
    wid = wid_of(handle)
    for tool, args in WRITE_TOOLS:
        r = await bob.call_tool(tool, {"handle": handle, **args})
        assert r.is_error and "unknown handle" in text(r), (tool, text(r))
    listed = await bob.call_tool("list_walkthroughs", {})
    assert wid not in json.dumps(listed.structured_content)
    with pytest.raises(Exception, match="Error creating resource"):
        await bob.read_resource(f"showntell://walkthroughs/{wid}")
    async with browser_as(srv, "bob@example.com") as (c, csrf):
        s = srv.store.get(wid)
        key = next(x.key for sl in s.w.slides for x in sl.says if x.key)
        for path in ("/", "data.json", "events", f"audio/{key}.wav", "poster.png", "assets/1-x.png", "exports/caching.html"):
            r = await c.get(f"/w/{wid}/{path}" if path != "/" else f"/w/{wid}/")
            assert r.status_code == 404, path
        post = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
        r = await c.post(f"/w/{wid}/report", json={"message": "x"}, headers={**post, "X-Report-Key": s.w.report_key})
        assert r.status_code == 404
        assert (await c.post(f"/w/{wid}/delete", json={}, headers=post)).status_code == 404
        assert wid not in (await c.get("/")).text
    assert srv.store.get(wid) is not None


async def test_two_users_share_and_unshare(srv, voiced):
    async with mcp_as(srv, "alice@example.com") as alice, mcp_as(srv, "bob@example.com") as bob:
        r = await start(alice)
        handle, wid = r["handle"], wid_of(r["handle"])
        assert not (await alice.call_tool("add_slides", {"handle": handle, "markdown": BODY})).is_error
        await voiced(srv.store.get(wid))
        await b_is_locked_out(srv, handle, bob)

        r = await alice.call_tool("share_walkthrough", {"handle": handle, "add": ["Bob@Example.com"]})
        assert not r.is_error and r.structured_content["shares"] == ["bob@example.com"]
        # Bob may watch now, but still not change anything.
        for tool, args in WRITE_TOOLS:
            r = await bob.call_tool(tool, {"handle": handle, **args})
            assert r.is_error and "unknown handle" in text(r), tool
        rows = (await bob.call_tool("list_walkthroughs", {})).structured_content["walkthroughs"]
        assert [(x["id"], x["role"], "handle" in x) for x in rows] == [(wid, "shared", False)]
        listed = text(await bob.call_tool("list_walkthroughs", {}))
        assert "shared with you by alice@example.com" in listed and handle not in listed
        async with browser_as(srv, "bob@example.com") as (c, csrf):
            assert (await c.get(f"/w/{wid}/")).status_code == 200
            data = (await c.get(f"/w/{wid}/data.json")).json()
            assert data["title"] == "Caching" and "owner" not in data and "shares" not in data
            assert (await c.get(f"/w/{wid}/{data['slides'][0]['says'][0]['audio']}")).status_code == 200
            post = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
            r = await c.post(f"/w/{wid}/report", json={"message": "x"}, headers={**post, "X-Report-Key": srv.store.get(wid).w.report_key})
            assert r.status_code == 200
            assert (await c.post(f"/w/{wid}/delete", json={}, headers=post)).status_code == 404
            gallery = (await c.get("/")).text
            assert wid in gallery and "shared by alice@example.com" in gallery and 'class="del"' not in gallery

            # Bob's live player is cut off as soon as Alice removes the share.
            async with c.stream("GET", f"/w/{wid}/events") as stream:
                lines = stream.aiter_lines()
                assert (await anext(lines)).startswith("data: ")
                r = await alice.call_tool("share_walkthrough", {"handle": handle, "remove": ["bob@example.com"]})
                assert r.structured_content["shares"] == []

                async def drain() -> None:
                    async for _ in lines:
                        pass

                await asyncio.wait_for(drain(), 5)
        await b_is_locked_out(srv, handle, bob)


async def test_sharing_with_the_company(srv, voiced):
    async with mcp_as(srv, "alice@example.com") as alice:
        r = await start(alice)
        handle, wid = r["handle"], wid_of(r["handle"])
        assert not (await alice.call_tool("share_walkthrough", {"handle": handle, "company": True})).is_error
        async with browser_as(srv, "carol@example.com") as (c, _):
            assert (await c.get(f"/w/{wid}/data.json")).status_code == 200
            await alice.call_tool("share_walkthrough", {"handle": handle, "company": False})
            assert (await c.get(f"/w/{wid}/data.json")).status_code == 404


async def test_shares_are_checked_for_shape_and_domain(srv):
    async with mcp_as(srv, "alice@example.com") as alice:
        handle = (await start(alice))["handle"]
        r = await alice.call_tool("share_walkthrough", {"handle": handle, "add": ["someone@elsewhere.org"]})
        assert r.is_error and "outside this company's domains" in text(r)
        r = await alice.call_tool("share_walkthrough", {"handle": handle, "add": ["not an email"]})
        assert r.is_error and "not an email address" in text(r)
        assert srv.store.get(wid_of(handle)).w.shares == []


async def test_owner_sees_their_own_with_handles_and_can_delete(srv):
    async with mcp_as(srv, "alice@example.com") as alice:
        handle = (await start(alice))["handle"]
        rows = (await alice.call_tool("list_walkthroughs", {})).structured_content["walkthroughs"]
        assert [(x["handle"], x["role"]) for x in rows] == [(handle, "yours")]
        src = await alice.read_resource(f"showntell://walkthroughs/{wid_of(handle)}")
        assert "How caching works" in src.contents[0].text
    async with browser_as(srv, "alice@example.com") as (c, csrf):
        r = await c.post(f"/w/{wid_of(handle)}/delete", json={}, headers={"Content-Type": "application/json", "X-CSRF-Token": csrf})
        assert r.status_code == 200 and srv.store.get(wid_of(handle)) is None


async def test_exports_stay_on_the_server_for_the_owner(srv, voiced):
    async with mcp_as(srv, "alice@example.com") as alice:
        handle = (await start(alice))["handle"]
        await voiced(srv.store.get(wid_of(handle)))
        r = await alice.call_tool("export_walkthrough", {"handle": handle, "format": "html", "path": "/tmp/x.html"})
        assert r.is_error and "cannot save to /tmp/x.html" in text(r)
        r = await alice.call_tool("export_walkthrough", {"handle": handle, "format": "html"})
        assert not r.is_error, text(r)
        url = r.structured_content["download"]
        assert "path" not in r.structured_content and url.endswith("/exports/caching.html")
    async with browser_as(srv, "alice@example.com") as (c, _):
        r = await c.get(url)
        assert r.status_code == 200 and r.headers["content-disposition"] == "attachment" and b"<html" in r.content


async def test_rpc_and_cross_site_embedding_are_off(srv):
    async with browser_as(srv, "alice@example.com") as (c, _):
        r = await c.post("/api/rpc/list", json={}, headers={"Authorization": "Bearer rpc-token"})
        assert r.status_code == 404
        r = await c.get("/static/embed.js")
        assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


# -- disk and network ---------------------------------------------------------------------------------


async def test_project_dir_is_refused(srv, tmp_path):
    async with mcp_as(srv, "alice@example.com") as alice:
        r = await alice.call_tool("start_walkthrough", {"title": "T", "project_dir": str(tmp_path)})
        assert r.is_error and "cannot read project_dir" in text(r) and "put the code in the Markdown" in text(r)
        assert srv.store.list(10, User("showntell-dev-login", "alice@example.com", "alice@example.com")) == []


@pytest.mark.parametrize(
    "block, code",
    [
        ("```python file=../../etc/passwd\n```", "source_error"),
        ("```python file=/etc/passwd lines=1-3\n```", "source_error"),
        ("```python file=src/app.py\n```", "source_error"),
        ("```diff file=src/app.py rev=HEAD~1\n```", "source_error"),
        ("```python file=src/app.py rev=HEAD~1\n```", "source_error"),
        ("```python old file=a.py\n```\n\n```python new file=a.py\n```", "source_error"),
        ("```svg file=../secret.svg\n```", "source_error"),
        ("```drawio file=/etc/arch.drawio\n```", "source_error"),
        ("![diagram](../../etc/passwd.png)", "image_error"),
        ("![diagram](/Users/someone/shot.png)", "image_error"),
        ("![metadata](http://169.254.169.254/latest/meta-data/)", "image_error"),
        ("![remote](https://example.com/a.png)", "image_error"),
    ],
)
async def test_disk_git_and_remote_sources_are_refused(srv, block, code):
    async with mcp_as(srv, "alice@example.com") as alice:
        handle = (await start(alice))["handle"]
        r = await alice.call_tool("add_slides", {"handle": handle, "markdown": f"## Source {{#src}}\n\n{block}\n\nThis shows it.\n"})
        assert not r.is_error
        issues = r.structured_content["issues"]
        assert any(i["code"] == code for i in issues), issues
        slide = next(sl for sl in srv.store.get(wid_of(handle)).w.slides if sl.id == "src")
        assert slide.kind == "text" and "passwd" not in json.dumps(slide.visual) and "169.254" not in json.dumps(slide.visual)
        assert not (srv.store.walk_dir(wid_of(handle)) / "assets").exists()


async def test_the_agent_is_told_to_put_code_in_the_markdown(srv):
    async with mcp_as(srv, "alice@example.com") as alice:
        r = await start(alice)
        assert "**On this server:**" in r["message"] and "put the content in the Markdown" in r["message"]
        tools = {t.name: t for t in (await alice.list_tools()).tools}
        assert "share_walkthrough" in tools
        assert "Leave empty" in tools["start_walkthrough"].input_schema["properties"]["project_dir"]["description"]


def test_local_text_is_unchanged_by_the_hosted_overrides():
    from showntell.server import guide, texts

    assert texts("link", False)["instructions"] == (hosted_text_dir().parent / "instructions.md").read_text()
    assert "On this server" not in guide("landscape")
    assert "share_walkthrough" not in texts("link", False)["tools"]


def hosted_text_dir():
    from showntell.server import TEXT

    return TEXT / "hosted"


# -- headers and CSRF ---------------------------------------------------------------------------------


async def test_every_response_carries_the_csp(srv):
    async with mcp_as(srv, "alice@example.com") as alice:
        wid = wid_of((await start(alice))["handle"])
    async with browser_as(srv, "alice@example.com") as (c, _), httpx.AsyncClient(base_url=srv.url) as anon:
        for client, path in [
            (c, "/"),
            (c, f"/w/{wid}/"),
            (c, f"/w/{wid}/data.json"),
            (c, "/w/" + "z" * 22 + "/"),
            (c, "/static/player.css"),
            (anon, "/"),
            (anon, "/health"),
        ]:
            r = await client.get(path)
            assert r.headers["content-security-policy"] == hosted.CSP, path
            assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
        page = (await c.get(f"/w/{wid}/")).text
    assert "<script>" not in page and not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page.replace('type="module" src', "src"))
    assert "script-src 'self';" in hosted.CSP and "unsafe-eval" not in hosted.CSP


async def test_the_session_cookie_has_its_flags(tmp_path, narrator, free_port):
    async with running(tmp_path, free_port, narrator) as s:
        s.hosted.identity.secure_cookie = True
        async with httpx.AsyncClient(base_url=s.url) as c:
            r = await c.get(f"/dev/login?token={TOKENS['alice@example.com']}", follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert r.status_code == 303 and cookie.startswith(f"{SESSION_COOKIE}=")
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age="))


async def test_posts_need_the_csrf_token_and_same_origin(srv):
    async with mcp_as(srv, "alice@example.com") as alice:
        wid = wid_of((await start(alice))["handle"])
    key = srv.store.get(wid).w.report_key
    async with browser_as(srv, "alice@example.com") as (c, csrf):
        base = {"Content-Type": "application/json", "X-Report-Key": key}
        cases = [
            (base, 403),
            ({**base, "X-CSRF-Token": "wrong"}, 403),
            ({**base, "X-CSRF-Token": csrf, "Origin": "https://evil.example"}, 403),
            ({**base, "X-CSRF-Token": csrf, "Sec-Fetch-Site": "cross-site"}, 403),
            ({"X-CSRF-Token": csrf, "X-Report-Key": key, "Content-Type": "text/plain"}, 415),
            ({**base, "X-CSRF-Token": csrf, "Origin": srv.url}, 200),
        ]
        for headers, status in cases:
            r = await c.post(f"/w/{wid}/report", content=b'{"message": "x"}', headers=headers)
            assert r.status_code == status, (headers, r.text)
        r = await c.post(f"/w/{wid}/delete", json={}, headers={"Content-Type": "application/json"})
        assert r.status_code == 403 and srv.store.get(wid) is not None


# -- fair use -----------------------------------------------------------------------------------------


async def test_two_users_speech_interleaves_through_the_store(tmp_path, idle_narrator, free_port):
    async with running(tmp_path, free_port, idle_narrator) as s:
        async with mcp_as(s, "alice@example.com") as alice, mcp_as(s, "bob@example.com") as bob:
            long = OPENING + "\n".join(f"\nParagraph {i} of a long one.\n" for i in range(6))
            await alice.call_tool("start_walkthrough", {"title": "Long"})
            ha = (await alice.call_tool("list_walkthroughs", {})).structured_content["walkthroughs"][0]["handle"]
            await s.service.add(ha, long, user=User("showntell-dev-login", "alice@example.com", "alice@example.com"))
            await bob.call_tool("start_walkthrough", {"title": "Short"})
            hb = (await bob.call_tool("list_walkthroughs", {})).structured_content["walkthroughs"][0]["handle"]
            await s.service.add(hb, "# Short\n\nBob's opening.\n", user=User("showntell-dev-login", "bob@example.com", "bob@example.com"))
    texts = [idle_narrator._q.get_nowait()[2].spoken.text for _ in range(idle_narrator.backlog())]
    assert "Bob's opening" in texts[1], texts[:3]


@pytest.fixture
async def tight(tmp_path, narrator, free_port):
    lim = Limits(narration_min=0.25, slides=3, writing=1, walkthroughs=2, storage_gb=2, calls_per_min=1000, markdown_kb=2)
    async with running(tmp_path, free_port, narrator, limits=lim) as s:
        yield s


async def test_slide_and_narration_limits_refuse_the_call_and_keep_the_walkthrough(tight):
    async with mcp_as(tight, "alice@example.com") as alice:
        handle = (await start(alice))["handle"]
        three = "".join(f"## Slide {i}\n\nA short line.\n\n" for i in range(3))
        r = await alice.call_tool("add_slides", {"handle": handle, "markdown": three})
        assert r.is_error and "limit of 3" in text(r)
        s = tight.store.get(wid_of(handle))
        assert len(s.w.slides) == 1 and "Slide 0" not in s.source
        r = await alice.call_tool("add_slides", {"handle": handle, "markdown": "## Next\n\nOne more slide here.\n"})
        assert not r.is_error, text(r)
        long = "## Long\n\n" + " ".join(["word"] * 60) + "\n"
        r = await alice.call_tool("add_slides", {"handle": handle, "markdown": long})
        assert r.is_error and "minutes per walkthrough" in text(r)
        assert [sl.title for sl in s.w.slides] == ["How caching works", "Next"]
        r = await alice.call_tool("revise_slide", {"handle": handle, "slide": s.w.slides[1].id, "markdown": " ".join(["word"] * 60)})
        assert r.is_error and "minutes per walkthrough" in text(r)
        r = await alice.call_tool("add_slides", {"handle": handle, "markdown": "## Big\n\n" + "x " * 1500})
        assert r.is_error and "KB of Markdown" in text(r)


async def test_walkthroughs_in_progress_and_stored_are_limited(tight):
    async with mcp_as(tight, "alice@example.com") as alice, mcp_as(tight, "bob@example.com") as bob:
        handle = (await start(alice))["handle"]
        r = await alice.call_tool("start_walkthrough", {"title": "Second"})
        assert r.is_error and "in progress" in text(r) and handle in text(r)
        assert not (await bob.call_tool("start_walkthrough", {"title": "Bob's"})).is_error  # per user
        assert not (await alice.call_tool("finish_walkthrough", {"handle": handle})).is_error
        assert not (await alice.call_tool("start_walkthrough", {"title": "Second"})).is_error
        tight.store.get(
            wid_of((await alice.call_tool("list_walkthroughs", {})).structured_content["walkthroughs"][0]["handle"])
        ).w.status = "finished"
        for s in tight.store.sessions.values():
            s.save_now()
        r = await alice.call_tool("start_walkthrough", {"title": "Third"})
        assert r.is_error and "2 walkthroughs stored" in text(r)


async def test_storage_is_limited(tmp_path, narrator, free_port):
    async with running(tmp_path, free_port, narrator, limits=Limits(storage_gb=1e-9)) as s:
        async with mcp_as(s, "alice@example.com") as alice:
            await start(alice)
            r = await alice.call_tool("start_walkthrough", {"title": "Second"})
            assert r.is_error and "GB" in text(r)


async def test_tool_calls_are_rate_limited_per_user(tmp_path, narrator, free_port):
    async with running(tmp_path, free_port, narrator, limits=Limits(calls_per_min=3)) as s:
        async with mcp_as(s, "alice@example.com") as alice, mcp_as(s, "bob@example.com") as bob:
            got = [(await alice.call_tool("list_walkthroughs", {})).is_error for _ in range(4)]
            assert got == [False, False, False, True]
            assert "3 tool calls a minute" in text(await alice.call_tool("list_walkthroughs", {}))
            assert not (await bob.call_tool("list_walkthroughs", {})).is_error


async def test_retention_counts_from_the_last_view(tmp_path):
    from showntell.prune import prune

    walks = tmp_path / "walkthroughs"
    old = time.time() - 200 * 86400
    for wid, viewed in (("seen", time.time()), ("unseen", 0.0)):
        (walks / wid).mkdir(parents=True)
        (walks / wid / "state.json").write_text(json.dumps({"id": wid, "updated": old, "viewed": viewed, "slides": []}))
    gone = prune(tmp_path, 180 * 86400)
    assert [g["id"] for g in gone["walkthroughs"]] == ["unseen"]


async def test_chromium_jobs_share_a_server_wide_limit(srv):
    sem = srv.hosted.browsers
    assert sem._value == 2
    async with sem, sem:
        assert sem.locked()
